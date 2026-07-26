# Matching Convergence — one thread, two engines, a defined cutover

**Both matching efforts now run in this thread.** Corrected after reading canon's real state
(2026-07-25): canon is **not** early — it is a mature, gated engine. This is a **strangler-fig
cutover of a nearly-complete engine**, not "build the missing piece."

## The two engines, as they actually are

**Unifyd** (this repo, DuckDB/Parquet): `build_product_master.py` → `xwalk_source_sku`/`dim_item`,
`identity_resolve.py`. Fast, deterministic, always-on in the dispatcher. **Feeds the LIVE serving
surfaces today** (velocity, dim_item, marts). Measured by `master_quality.py`: **P=1.000, R=0.285** —
never over-merges, under-merges hard.

**Hoodie-canon** (`../hoodie-canon`, Postgres+pgvector): a full **4-tier match cascade**, Gate 5 green:
- **tier0** — exact key/alias on `external_keys` (UPC/COLA), 0 model calls → the deterministic
  UPC-collapse I almost duplicated in unifyd. **It already exists here.**
- **tier1** — blocked deterministic fuzzy, 0 calls → matched | new_entity
- **tier2** — pgvector ANN shortlist, 0 calls → candidates
- **tier3** — Claude (Haiku) adjudicator + **review queue** → matched | new_entity | review → writeback
- writes to the rev-0010 entity layer: item spine, SCD-2 attributes, key crosswalk, HNSW embedding.

**Correction to the earlier draft:** there is **no "unifyd Stage-0" to build** — canon's tier0/tier1
*are* the deterministic stages, done properly. The recall lift comes from **canon's cascade + the
in-flight 0011 hardening** (carry UPC into `external_keys` so tier0 fires on retail rows), not a new
unifyd engine. Building one would be the exact duplication we're avoiding.

## Coexist — during the cutover (not permanent rivals)

- **Unifyd keeps serving** the live surfaces on its provisional `item_key` (R=0.285) until canon's
  authoritative identity is wired to the serving path. Velocity can't wait on canon's review loop for
  every new SKU — the legacy master stays the interim.
- **Canon computes the authoritative identity** offline (its Postgres cascade), scored on the shared
  ruler, until it supersedes.
- **Unifyd's cheap deterministic collapse may remain as a candidate pre-filter into canon** — optional,
  only if it saves canon work; never a competing authority.

## Collapse — the three single sources of truth

1. **One ruler — `gold_matches` + `master_quality`.** Already built in unifyd. Canon's `subject`
   decisions get scored on the SAME pairs → canon P/R comparable to unifyd's 1.000/0.285. This is how
   we say "canon is better, here's the number," and the target canon ratchets.
2. **One identity vocabulary.** UPC + brand/size normalization mean the same thing in both — canon's
   `external_keys` normalization and unifyd's UPC crosswalk read one spec.
3. **One served answer — the `item_identity` contract.** `(source, product_id) → canonical_id,
   ruled_by ∈ {unifyd-legacy, canon-authoritative}, confidence, as_of`, landed in the warehouse so the
   serving path (velocity/dim_item/marts) reads *it*, never an engine's internals. Canon supersedes.
   The cutover is then a data change, not a rewrite.

## Sequence — the cutover, in this thread

| Step | What | Repo | Status |
|---|---|---|---|
| **S1** | Land canon's in-flight **0011 real-data hardening** (UPC→external_keys so tier0 fires; one-open-review-per-observation). Verify its gate. | canon | **DONE** — completed the force-stopped edit (2 test-seed gaps); canon full suite 1020 green; landed on canon main. |
| **S2** | Score canon's resolved identities on the same UPC signal as unifyd → the head-to-head. | canon + shared | **DONE** — `src/match/quality.py` (canon-side ruler): **canon P=1.000 R=1.000** vs **unifyd P=1.000 R=0.285**. Canon's tier0 exact-key fixes the under-merge. Honest caveat: dev-scale (28 same-UPC groups / 29 entities); full-scale needs the ingest loaded. |
| **S2.5** | Run the full retail universe through the cascade at scale; measure P/R; pull every free lever before the LLM. | canon | **DONE** — 91,841 obs → **89,715 entities** with ZERO model calls / ZERO embeddings (`max_tier=1`): **P=0.9977 R=0.9872 F1=0.9925** (vs unifyd R=0.285). Then wired the **free tier2 auto-accept lever** (cosine ≥ 0.95 auto-links, no LLM): with real MiniLM embeddings, **26.4% of the 27,034 provisional mints have a ≥0.95 twin**; converged the master would drop **89,715→73,892 entities (17.6%, $0)**. Canon commits 34cbfed / 5c1e222 / 9ddbfaf; 1025 tests green. |
| **S3** | Freeze the `item_identity` contract; export canon's authoritative identity so unifyd's serving path can read it. | both | **DONE** — contract FROZEN. Canon-side export (`canon/src/index/export_identity.py`, dc39d71): one JSONL record per source SKU (source, product_id, **canon_item_id**, attrs, upcs, status/merged_into, provenance). Unifyd-side ingest (`unifyd/ingest_canon_identity.py`, PR #592): lands it as the `item_identity` warehouse table. Verified end-to-end: **90,381 records / 89,686 items across 9 sources; 200 items span multiple retailers** (offprem+kroger etc.) — the cross-source identity `item_key` fragmented, now served under one `canon_item_id`. |
| **S4** | Cut velocity/dim_item/marts to read `item_identity` (canon supersedes unifyd `item_key`); `master_quality` scores the SERVED identity. | unifyd | **Slice 1 DONE** (strategy A, overlay+score): `master_quality.score_canon()` scores the served `canon_item_id` against `item_key` on the SAME gold — `python master_quality.py --canon` (PR #596, additive, own `master_quality_canon` table, 18/18). Test proof: where item_key under-merges (R<0.5), canon lifts R→1.0 at P=1.0. **Gate:** run the prod head-to-head (Tigris has `retail_observations`+`xwalk`+`item_identity`) to confirm the lift, THEN slice 2 = the serving-metric overlay (corroboration grouped by `canon_item_id` in `server.py`/`wb_views.py`). No live surface changed yet. |
| **S5** | Retire unifyd matching to the deterministic pre-filter (or drop it); canon authoritative for all identity. | both | strangler-fig complete |

## Prerequisites / open checks

- Canon needs its **PG16+pgvector dev DB** up to run migrations + the cascade (`CANON_DB__DSN`,
  default `postgresql+psycopg://localhost/canon`).
- **Ingest seam BUILT + RUN AT SCALE (`canon/src/acquire/bridge.py`).** Unifyd exports the retail
  identity universe (`/tmp/canon_ingest_products.jsonl` — **90,384 distinct UPC'd products**); the
  bridge lands them as observations with the UPC on `external_keys` so tier0 resolves them (idempotent).
  Loaded → **91,841 observations** in canon. This is the interim seam until canon's own acquire covers
  the retail catalogs (strangler-fig).
- **At-scale run DONE (S2.5) — the head-to-head now holds at scale, not just dev-scale.** The full
  universe resolved to **89,715 master entities at P=0.9977 R=0.9872** (vs unifyd R=0.285 on the same
  signal) — the recall lift is real and holds. Precision floor is conservative: the 204 multi-UPC
  "over-merges" are tier1 same-brand+name+size auto-links across DIFFERENT UPCs (the deferred cross-UPC
  question); 3 recall splits = UPC leading-zero variance.
- **Levers before the LLM (the cost doctrine, per user 2026-07-26: "pull every lever before falling
  back to LLM — better product AND cheaper").** The cascade is cost-ordered; `resolve()/run_observations`
  now take **`max_tier`** to cap the descent. `max_tier=1` = deterministic (tiers 0-1, $0); `max_tier=2`
  adds the **free local embedding lever** (tier2 auto-accept: cosine ≥ `match.tier2_auto_accept`=0.95
  auto-links with NO model call, $0); `max_tier=3` sends only the ambiguous residue to the paid Haiku
  adjudicator. At-scale tier mix (max_tier=1): **3.5% tier0 · 67.6% tier1 · 29.0% provisional mints** —
  and with real MiniLM embeddings (clean text), **26.4% of those provisional mints have a ≥0.95 twin**
  the free lever auto-resolves; a converged merge would drop the master **89,715→73,892 (17.6%, $0)**.
  Tier2 = LOCAL + FREE (sentence-transformers, `uv sync --extra embeddings`); only tier3 costs money.
  (The converged figure is a single-linkage upper bound — chaining through thin-name hubs is exactly the
  gray zone the LLM/Gate-5 golden adjudicates; applying merges mutates the master and is precision-gated,
  not automatic.)
- The `gold_matches` schema is the shared asset — canon reads it as its eval + review seed; keep it
  the one truth.

**The one-line rule (now correctly placed):** *canon is the identity engine; unifyd serves the interim
answer and grades both on one gold set; one contract carries the verdict to the surfaces; the cutover
is a data change, not a second engine.*
