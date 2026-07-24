# Near-Real-Time Plan — Hoodie Suite at 250M+ scale

**Goal:** the site reflects fresh market data continuously (near real time by tier), while the
architecture handles ~250M MDM source records plus a SipSource-class depletion feed
(300–600M rows at its most aggregated grain). **Principle: spend engineering up front so the
recurring bill stays near zero** — cheap object storage + ephemeral compute, no always-on
warehouse, no new SaaS until a documented decision gate is hit.

---

## 0. What "near real time" means here (freshness SLOs, not one number)

Real time is per-tier, because cost scales with cadence and most data doesn't change hourly:

| Tier | What | Cadence / SLO | Examples |
|---|---|---|---|
| **HOT** | Per-store price + in/out diffs from cracked recipes | every 1–4h, data ≤4h old | abc-fws snapshot diff, Kroger atlas, Total Wine getProduct, 7NOW |
| **DAILY** | Full catalog sweeps | 1×/day, ≤24h old | Binny's, Spec's, Target, offprem census, aggregators |
| **WEEKLY** | Registries, locators, reference | 1×/wk | TTB, control states, CA ABC, Census |
| **FEED** | Bulk file drops (SipSource, Grepsr) | queryable ≤1h after the file lands | SipSource monthly/weekly drop, Grepsr S3 by 05:00 UTC |
| **MASTER** | dim_* rebuild lag behind any source refresh | ≤1 worker cycle (target: hourly) | dim_item, dim_outlet, marts |

SipSource is a delivered file, not a scrape — "NRT" for it means the ingest→mart pipeline is
automatic and fast, not that we poll it hourly.

---

## 1. Storage engine hardening — the scale wall (do this FIRST)

Everything else sits on this. Three current behaviors break at 250M:

1. **`warehouse.write_accumulate` materializes the whole table in Python** (reads every row
   into list-of-dicts, merges in Python, rewrites one file). O(table) RAM + network per merge.
   Fine at 100k rows; impossible at 250M.
2. **One `.parquet` file per table.** No partition pruning, every query scans everything, every
   merge rewrites everything, and a 600M-row table is one multi-GB object rewritten per touch.
3. **`list_datasets` does a footer read per file** (already needed 24 threads at ~170 files).

### 1a. Partitioned datasets become the default for anything big
- Layout: `warehouse/<table>/<part_key>=<val>/part-*.parquet`, zstd, ~64–128MB row groups,
  rows **sorted by the pruning key** inside each part.
- Partition keys by table class:
  - observations / time-series: `date` + `source` (already the `write_partition` shape — keep)
  - source catalogs (src_*): `source` (and `state`/`market` where natural)
  - SipSource: `period` + `state` (or distributor)
  - dim_* masters: `bucket` = hash-prefix of the entity id (e.g. 256 buckets), so incremental
    rebuilds rewrite only touched buckets
- **Rule:** any table expected >5M rows, or any accumulating table, must be partitioned. Small
  tables keep the single-file path — don't churn what works.

### 1b. Rewrite merge as a columnar operation
- `write_accumulate` v2: DuckDB anti-join (`existing WHERE key NOT IN (new)` ∪ new) executed
  **per affected partition only**, streamed to a new part file, atomic swap. Never a Python
  list-of-dicts. Memory stays bounded by one partition, not the table.
- Keep the empty-clobber guard and verify-landing semantics exactly as they are — they're the
  hygiene layer that already works.

### 1c. Manifest instead of footer-scans
- A `_manifest` table (table, partition, rows, min/max of pruning key, bytes, updated_at),
  updated on every write. `list_datasets`, freshness checks, and the Data Console read the
  manifest — one small read instead of hundreds of S3 round-trips. The manifest is also the
  **change log the incremental master build consumes** (§4).

### 1d. SipSource landing (and any future bulk feed)
- Stream file → partitioned parquet with `write_parquet_from_csv`-style columnar reads
  (never through Python dicts), string-typed IDs, sorted, zstd.
- 300–600M rows ≈ roughly 15–40GB parquet — comfortably fine on Tigris (object storage is the
  cheap part; this is why the architecture holds).
- **The site never queries the raw grain.** Ingest immediately builds aggregate marts
  (brand × market × period, supplier roll-ups) of a few million rows; the raw grain exists for
  drill-down jobs on the worker, not for request-path queries.

**Exit criteria:** a merge into a 100M-row table completes in minutes on the worker with <8GB
RAM; a scoped site query (one brand/market) answers in <2s via partition pruning.

---

## 2. Compute topology — the cost lever

Three roles, each on the cheapest hardware that does the job:

| Role | Where | Cost profile |
|---|---|---|
| **Serve** | existing Fly shared-cpu-4x/4GB | always-on (already paid). Only reads small marts + manifest. **Never runs builds.** |
| **Build** | **GitHub Actions is already the ephemeral runner** (`cloud-sources.yml` daily, `scrape-runner.yml` 12h — land straight to Tigris). Master builds/compaction join those workflows. A dedicated Fly worker machine (~16GB, per-second billing) is a **gated upgrade**: adopt only when a build exceeds Actions' 6h/7GB limits. | Actions minutes ≈ free at current volume |
| **Scrape** | Mac (anti-bot headful, launchd — already running) + headless sources in the Actions runners | Mac is free; headless is cheap anywhere |

- DuckDB-on-Tigris stays the engine. **Snowflake decision gate** (don't pay for it early):
  adopt only when (a) request-path queries need cross-grain joins partition pruning can't
  serve, or (b) >2–3 concurrent heavy readers, or (c) worker builds exceed ~1h despite
  partitioning. Until then the Parquet layout above is deliberately Snowflake-loadable
  (same partitioning maps to clustering keys), so migration is a load, not a rewrite —
  that load is staged in `snowflake/` (registry-driven DDL + `COPY`; the seed to Unifyd).

---

## 3. Orchestration — from ad-hoc scripts to a due-scheduler

Today: launchd fires `run_sources.py` daily + hand-run `run_*.sh` queues. Replace with one
dispatcher, keep all the working parts (registry, verify-landing, source_runs):

- **Registry gains scheduling metadata** per source: `tier` (hot/daily/weekly),
  `interval_h`, `slo_h`, `cost_class` (free/proxy/BD), `runs_on` (mac/cloud).
- **`run_sources.py --due`**: computes what's due from `source_runs` (last success +
  interval), runs exactly those. Idempotent, lock-filed, safe to fire often.
- **Mac:** launchd every 30min → `--due --mac-only` (keeps the strict one-at-a-time
  anti-bot serialization, aggregators-first ordering moves into the registry as a priority
  field instead of living in run_mac_queue.sh).
- **Cloud:** the scheduled worker machine runs `--due --headless-only` hourly, then the
  build steps (§4), then compaction, then exits.
- **Hot tier is diff-shaped by design:** snapshot+diff (the abc-fws pattern) so an hourly
  touch costs a fraction of a full sweep. Promote a source to HOT only when its recipe
  supports cheap deltas.
- **Feeds:** worker checks the SipSource/Grepsr drop location each cycle; new file → ingest
  → marts, automatically.
- Promo-cadence awareness (Publix=Wed etc.) later plugs in as per-source `interval` overrides
  by weekday — a registry change, not an architecture change.

---

## 4. Incremental master — MDM that keeps up

The master must stop being a manual full rebuild (it currently doesn't auto-rebuild at all,
and a 250M-row full rebuild would be hours of compute per cycle):

1. **Delta detection:** each worker cycle diffs the `_manifest` against the last build
   watermark → the set of changed source partitions → changed source keys.
2. **Scoped re-shred + re-match:** only changed records go through shred → src_<grain> →
   candidate blocks → match. Candidate blocking is already the matching-at-scale approach;
   blocks live partitioned so a changed record touches a few buckets, not the world.
3. **Append-only SCD-2 stays the hard rule:** changes append new versions; the hash-bucket
   partitioning (§1a) means a cycle rewrites only touched buckets of dim_*.
4. **Marts rebuild last**, scoped to affected slices, and publish with a version stamp the
   serving layer keys on.

Result: master + site lag any source landing by ≤1 worker cycle (target hourly) at a compute
cost proportional to **what changed**, not what exists.

---

## 5. Serving — what the site actually reads

- `/api/*` reads **marts + manifest only**; raw grains and big src_* tables are worker-side.
- Every API response carries freshness (`as_of` from the mart's version stamp); the suite UI
  surfaces data age everywhere a number is shown — "near real time" you can *see and verify*.
- Short-TTL in-process cache keyed on mart version (ETag) so repeat dashboard loads don't
  re-hit Tigris; version bump = instant invalidation.
- The existing 4GB serving VM is sufficient indefinitely under this rule; it never needs to
  grow with data volume.

---

## 6. Monitoring & cost ledger

- **Freshness board** (Data Console): per source, tier SLO vs actual age from source_runs +
  manifest; red at 2× SLO. This is the honest "are we actually near-real-time" view.
- Degraded/empty/no-creds semantics stay as-is (they're good); add SLO-breach to the same
  ledger so one surface shows both "ran badly" and "hasn't run."
- **Cost ledger:** machine-seconds (Fly Machines API), proxy GB, BD calls per source per day
  — so the recurring cost is a number on a dashboard, not a surprise on an invoice.

---

## 7. Rollout order

| Phase | Work | Why this order |
|---|---|---|
| **1** | Warehouse v2: partitioned layout + DuckDB merge + manifest (§1a–c) — **BUILT** (`feat/warehouse-v2-partitioned`, 22-check compat suite) | everything depends on it; do BEFORE big data arrives |
| **2** | `--due` dispatcher (§2, §3) — **BUILT** (`feat/nrt-due-dispatcher`: registry `interval_h`/`priority`, ledger-driven due-ness, lock, run_due.sh + launchd template). Cloud runner = existing GH Actions; Fly worker deferred behind the §2 gate | moves builds off the serving VM, establishes the cycle |
| **3** | Master builds wired to the dispatcher (§4 as re-scoped by §8c) — **BUILT** (`BUILDS` registry: build-outlets + build-product-master run when an upstream source lands new rows, min-gap throttled, Mac-tick only so dim_* keeps one writer; deep incrementalism stays hoodie-canon's) | master starts tracking sources automatically |
| **4** | SipSource ingest + marts (§1d) — **SIMULATED & PROVEN** (`sipsource_sim.py` synthetic feed + `sipsource_ingest.py` marts; feed lands hive-partitioned period×market, raw NEVER served, dimension-BOUNDED marts). Proof at 50M→100M: raw doubles, serving mart stays 9.13M→9.24M (1.01×), conservation exact, scoped query ~30ms, 500M extrapolates to ~9.5GB raw / ~1–3min worker rebuild. `build-sipsource-marts` wired in the registry (disabled until the real feed lands). | lands on infrastructure already sized for it |
| **5** | Hot-tier promotion (diff recipes hourly) + freshness UI (§3, §5) | the visible "near real time" payoff |
| **6** | Cost ledger + SLO alerting (§6) — **BUILT** (`cost_ledger.py`: MEASURED machine-hours + rows/run from the ledger, MODELED infra $ by registry `cost_class` env-rated; surfaced in the Data Console cost strip via `monitor.build().cost`). Live verdict: ~$49.50/mo, ALL infra ($0 compute on free Mac+Actions), concentrated in 4 bd/proxy sources — the near-zero principle made verifiable. SLO breaches already surface via §5's slo_counts. | keeps the extended cost provably minimal |

**Standing decision gates:** Snowflake (§2 criteria) and any always-on compute >1 machine —
both require the gate to be demonstrably hit, documented in this file, before spending.

---

## 8. Building this without breaking anything else (cross-repo survey, 2026-07-21)

### 8a. What's in flight right now
- **"Hoodie Suite consolidation" session** — still open in the *main* hoodie-suite checkout
  with uncommitted edits to `unifyd/server.py`, `unifyd/analyze.py`, `apps/order-hub.html`.
  → NRT work happens on its own feature branch/worktree (mandatory anyway); any `server.py`
  changes (freshness API, §5) land LAST and rebase over whatever that session commits.
- **hoodie-canon `.venv` fix session** — running, disjoint surface. No conflict.
- Two idle hoodie-suite worktrees (size extraction, label-reader) — zero diff vs main today;
  the size-extraction one touches item-grain code, worth a glance before Phase 3 lands.
- **launchd is live ops**: `com.hoodie.sources` + `com.hoodie.ubereats` fire nightly on the
  Mac using the **hoodie-backend venv**. Every `warehouse.py` merge is immediately in the
  production write path that same night.

### 8b. Coupling map (who touches what the plan changes)

| Repo | Coupling | Breakage risk |
|---|---|---|
| **hoodie-backend** | `wh_sync.py` sys.path-imports `unifyd/warehouse.py` directly; TTB scrapers stream CSVs into the **same Tigris bucket** via `write_parquet_from_csv`; label images via `put_bytes`. Currently **9 commits ahead, unpushed.** | HIGH — warehouse.py's public functions are a de-facto cross-repo API |
| **hoodie-canon** | Strangler-fig by charter (ADR-001: "unifyd keeps running untouched; proven logic gets ported, not vendored"). Own PG16+pgvector store, own content-addressed blob layer (S3-shaped, Tigris-ready). Doesn't read the suite warehouse *yet*. | LOW today — but §4 overlaps its charter (see 8c) |
| **hoodie-app** | API-contract only: mirrors `unifyd/domain.py` types in `packages/core`; reads `/api/*`. | LOW — storage layout is invisible; freshness fields are additive |
| **hoodie-suite** itself | `server.py`, `analyze.py`, `monitor.py`, Data Console, `run_sources._rows` all read through `warehouse.query/row_count/list_datasets` (plus one direct footer read in `run_sources`). | Contained IF the warehouse API stays stable |

### 8c. Ownership boundary — the one plan change this forces
**§4 (incremental master) is hoodie-canon's job, not unifyd's.** Canon *is* the MDM rebuild;
building a second incremental-master engine inside unifyd would fork the effort ADR-001
exists to prevent. Re-scope:
- unifyd keeps its **existing** master builds running unchanged, just *scheduled* on the
  worker (automation, not a rewrite).
- The **`_manifest` change-log (§1c) is the seam**: unifyd publishes "which partitions/keys
  changed since watermark W" — exactly the ingest interface canon's raw-first pipeline wants.
  Canon consumes it when its ingest phase arrives; incrementalism investment goes there.
- Suite-side scope is therefore Phases 1, 2, 3, 5, 6 + SipSource landing; §4 shrinks to
  "automate the current builds + publish the change-log."

### 8d. Compatibility rules for Warehouse v2 (the non-negotiables)
1. **`warehouse.py`'s public API is frozen**: `write_parquet`, `write_accumulate`,
   `write_parquet_from_csv`, `write_partition`, `query`, `query_parts`, `row_count`, `uri`,
   `list_datasets`, `put_bytes`/`get_bytes` keep their signatures and semantics. v2 is a new
   engine *behind* those names, selected per table.
2. **Dual-read, manifest-driven**: the manifest records each table's layout
   (`single-file` | `partitioned`). `query`/`row_count`/`list_datasets` resolve through it and
   fall back to the current single-file path — so every reader in every repo (including
   hoodie-backend's, which we don't control from here) keeps working with zero changes.
3. **Migrate table-by-table, never big-bang**: new tables (SipSource) born partitioned;
   biggest accumulators migrated one at a time during a quiet window (no in-flight writer —
   dispatcher lock); the old single-file object is kept until the partitioned copy verifies
   (row counts + checksum queries match). Rollback = flip the manifest entry back.
   Small tables stay single-file forever.
4. **Empty-clobber guard and verify-landing survive intact** in both layouts.
5. **hoodie-backend sequencing**: its 9 unpushed commits get pushed/synced BEFORE any
   warehouse.py change merges, so both repos test against the same library. Its TTB tables
   migrate last, coordinated, since its writers run from a separate checkout.

### 8e. Safety harness before any of it merges
- **Golden compatibility tests** (`unifyd/tests/test_warehouse_compat.py`): identical call
  sequences against v1 and v2 layouts must return identical query results; covers
  accumulate-merge, empty-guard, partition pruning, manifest fallback. Wire into a
  lightweight GitHub Action (the repo currently has deploy-only CI).
- **Nightly-run canary**: after any warehouse merge, watch the next launchd pass in
  `source_runs` — all sources green before the next migration step proceeds.
- **Fly deploy order**: suite merge → Fly healthy (`/api/health`, dashboard loads) → then
  and only then the next table migration.

---

## 9. What comes after NRT

All six phases above are built (2026-07-24). The machine runs itself; the next campaign makes what
it produces the measured best of its peers — velocity engine calibrated against actual state sales,
proven master precision, honest projection, survivability. See **MOAT-PLAN.md**.

---

## 10. Scale corrections — SipSource raw tiers & master fan-in (2026-07-24)

Two corrected planning numbers, from the operator:
- **300–600M rows is SipSource's MOST AGGREGATED tier.** The underlying raw runs to **trillions of
  records**. Which tier we ingest depends on what we buy — the architecture must be sized for the
  worst case, not the sample.
- **Master intake: ~200–300M source item records → 800k–1.3M master items** (~240:1 fan-in).

### 10a. The feed pyramid, at measured constants (~18.4 B/row zstd; 5M rows/s object-storage-bound)

| Raw tier | Parquet | Tigris $/mo | Full pass (1 worker) | Monthly increment (1/37th) |
|---|---|---|---|---|
| 500M (aggregated — **proven**) | 9.2 GB | ~$0.18 | minutes | seconds |
| 10B | 184 GB | ~$3.70 | 0.6 h | ~2 min |
| 100B | 1.8 TB | ~$37 | 5.6 h | ~12 min |
| 1T | 18.4 TB | ~$368 | 55.6 h | **1.5 h** |

**The rules that make even the trillion tier survivable:**
1. **No job ever passes over full raw.** Everything is per-period incremental; the monthly
   increment at trillion-total is ~27B rows ≈ 1.5h on one worker — inside free Actions limits.
2. **The one full-history pass (initial backfill) is DECOMPOSED by period** — ~37 jobs of ≤1.5–6h
   each, run as an Actions matrix. Even a trillion-row backfill fits free compute; it is never one
   big job on a big machine.
3. **Three-layer pyramid, each bounded:** raw-as-delivered (cold archive, touched once per drop) →
   **working grain** (item×market×month ≈ 0.2–2.4B rows, 4–45GB — worker-side drill-downs) →
   serving marts (~9–50M, dimension-bounded — the ONLY thing the site reads). The 100M-row proof's
   bounded-mart property is exactly what makes serving immune to raw-tier choice.
4. **Gate updates:** Tigris leaves the free tier at ~250GB raw (~$5/mo — fine; $368/mo at 1T is a
   real line item on the cost ledger, still incumbent-impossible). The Snowflake/ClickHouse gate
   gains a concrete trigger: **a per-period increment exceeding ~6h on the largest viable worker.**

### 10b. Master fan-in at ~240:1 — the staged collapse

A couple hundred million item records are mostly **per-store/per-source repeats of the same ~1M
items** — so the fan-in is dominated by deterministic keys, and the expensive machinery only ever
sees the hard tail:

| Stage | Mechanism | In → out (est.) | Cost class |
|---|---|---|---|
| 0 | SQL exact collapse on normalized UPC/GTIN (checkless-13 heals etc.) | 250M → ~30–80M residual | pure DuckDB, minutes |
| 1 | Deterministic composite key (brand_norm + size_ml + class) | residual → ~5–15M clusters | pure DuckDB, minutes |
| 2 | Blocked similarity within (brand, category, size-band); price-coherence + category-tree scorers auto-accept/reject | ambiguous band only → ≤1–2M pairs | SQL + batch LLM (one-time, ~$100s) |
| 3 | Human exception queue, ranked impact × uncertainty | thousands, not millions | steward time |

- **Boundary (ADR-001 unchanged):** stages 0–2 scoring are warehouse-side SQL pushdown in unifyd;
  the durable identity engine for the hard tail is **hoodie-canon's charter**. unifyd hands canon
  ~5–15M collapsed clusters — never 300M raws.
- **Explicit flag:** today's `build_product_master.py` materializes source tables into Python row
  loops — correct at current volumes, **below this scale by design**. The staged collapse above is
  its successor path; do not grow the Python loop toward 200M.
- **Performance targets (scoreboard rows):** cold full re-master ≤ one weekend on one worker;
  daily incremental master cycle ≤ 10 min; both measured, not asserted.
