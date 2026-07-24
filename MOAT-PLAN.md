# Moat Plan — from "very good" to category-owning

**Companion to NRT-PLAN.md** (all six NRT phases built 2026-07-24: bucketed warehouse, --due
dispatcher, dispatcher-driven builds, SipSource pipeline proven at 500M, SLO surface, cost ledger).
NRT made the machine run itself; this plan makes what it produces **the best of its peers, measured —
not asserted.**

**The category we claim:** *the only near-real-time, store-level distribution & velocity system in
bev-alc, with provable lineage, measured accuracy, and a cost structure no incumbent can match.*

**Why incumbents can't follow:**
- **SipSource / NielsenIQ / Circana** sell lagged aggregates (brand×market×month, weeks behind) by
  construction — panel/depletion economics. They structurally cannot produce per-store, per-day
  inventory reality. We capture it hourly-to-daily and own the raw grain.
- **Their economics** need five-to-six-figure licenses; ours is ~$50/mo, on a dashboard (cost ledger).
- **Nobody proves their data.** Verify-landing, shrink guards, conservation checks, DETERMINISTIC-vs-
  INFERENCE labeling — provable honesty is already our habit; this plan extends it to accuracy itself.

**The honest gaps this plan closes** (where "good enough" currently hides):
1. Scraped shelf-presence is not market volume — no representativeness / projection / confidence yet.
2. The master is built but **unproven** — no measured precision/recall, no per-record confidence.
3. One Mac, cracked recipes, TCC fragility — a bus-factor liability no licensed-data peer carries.

**Doctrine carried over from NRT:** spend engineering, not money (decision gates before any new
recurring cost); append-only history; deterministic numbers with labeled inference; nothing surfaced
that can't cite its evidence.

---

## 0. What "best" means, measurably — the scoreboard

Every claim gets a number, and every number gets a surface. No adjectives without measurements.

| Claim | The number that proves it | Surfaced where | Initial target |
|---|---|---|---|
| Freshest in category | per-source SLO age vs interval | Data Console dispatcher strip (live) | hot ≤4h, daily ≤24h (live today) |
| Cheapest in category | est $/mo, measured machine-hours | Data Console cost strip (live) | ≤$100/mo at full fleet |
| **Velocity you can trust** | MAPE of implied velocity vs ground-truth sales (Iowa + control states) at brand×market×month | Velocity tab + this doc | ≤15% v1, ≤10% v2 |
| **Master you can trust** | precision / recall on a versioned gold set; % records carrying confidence | MDM workbench + CI gate | P ≥99%, R ≥95% @ item grain, 100% confidence coverage |
| **Honest projection** | coverage % per market×channel cell; CI width; anchor-validation error | Coverage map + every market metric | 100% of cells labeled; validated cells within ±10% of anchors |
| Survives its author | tested cold-start time; recipe MTTR | runbook + health digest | restore ≤1h; MTTR ≤72h |

Targets are v1 stakes-in-the-ground — re-baseline after the first calibration run, but only ever
tighten publicly.

---

## Workstream V — the Velocity Engine (the crown jewel)

**The insight:** per-store inventory deltas ARE demand data. A store's count going 40→12→38 across
scrapes is sell-through and a restock, per store, per day — a signal SipSource (monthly, distributor
grain) and Nielsen (panel, lagged) structurally cannot produce. The raw material already lands:
`retail_observations` (observe.py) is the dated per-(store, product) price/qty/in-stock time-series,
partitioned per (date, source), with **numeric counts** from Spec's, Kroger atlas, Total Wine, 7NOW,
Binny's, hemp-inventory — and in/out state from abc-fws and the rest.

### V1. Observation-quality layer (know your instrument before you trust it)
- `obs_quality` build: per (source, store, sku) — observation cadence (median gap, days observed),
  qty-jitter fingerprint (count wobble without price/promo change = shelf-count noise), coverage of
  the store's catalog. This is the per-cell error model everything downstream cites.
- Junk detection: sources whose qty is a status-bucket in disguise, stores that report stale numbers.

### V2. Delta decomposition (the estimator)
Classify every consecutive observation pair per (store, sku):
- **SALE**: qty down → implied units sold (the workhorse: sum of negative deltas between restocks —
  the standard inventory-decrement method).
- **RESTOCK**: qty up → delivery event. Also a signal by itself: restock cadence per store/chain
  (feeds the promo-calendar cadence learning too).
- **OOS enter/exit**: qty hits 0 / recovers → censored demand, tracked as lost-sales exposure and
  as a **distribution-void event** (sales opportunity — surfaced as good news, with the next move).
- **CENSORED**: observation gap too long to attribute (restock could hide inside) → excluded from
  the estimate, counted against confidence. Never silently included.
- **NOISE**: sub-threshold wobble per V1's fingerprint → damped.

### V3. `fact_velocity` (the product)
- Grain: store × sku × week (+ brand×market×week rollup mart, dimension-bounded per the SipSource
  pattern). Fields: implied_units, days_observed, censored_days, oos_days, restock_events,
  **confidence 0–1** (from cadence, censoring rate, V1 noise). Partitioned per warehouse rules;
  rebuilt by the dispatcher (a `build-velocity` BUILDS entry, `after` the observation sources).
- Every downstream number inherits and displays the confidence. Cells below a floor are suppressed,
  not shown — the honesty rule.

### V4. Calibration against ground truth (what makes it *provably* right)
This is the step none of the scraped-data shops do, and we can, because bev-alc has public answers:
- **Iowa**: every Class-E spirits transaction since 2012 (BigQuery public mirror, `iowa_bq.py`,
  ~30M rows) — store-level actual sales. The gold anchor.
- **Control states**: OR / UT / NC / MT monthly actuals already landing (`control_state.py`).
- Join implied velocity to actuals at brand×market×month where footprints overlap → fit the scaling
  factor, publish **MAPE** — the scoreboard's headline number. Re-run monthly as a standing build;
  regression in MAPE = a red SLO, not a quiet drift.
- **SipSource, when it arrives, is the third anchor — a corroborator, never the spine** (same
  doctrine as TTB-not-spine, VIP-not-spine).

### V5. Derived signals (what sales teams actually buy)
- **Movers**: velocity leaderboards, accelerating/decelerating brands per market (positive framing:
  lead with the win + the next move).
- **Voids**: store carries the category but not the brand; brand OOS ≥N days with estimated lost
  units. An actionable list with evidence, not a chart.
- **Restock cadence** per store/chain → order-timing intelligence + promo-flip detection.

### V6. Surfaces
- `/api/velocity` (marts only, confidence + as_of on every payload) + a Velocity tab in the console
  and the Hoodie app. Nothing renders without its confidence.

**Exit criteria:** MAPE ≤15% vs Iowa at brand×market×month on covered brands; ≥100k store×sku cells
at confidence ≥0.7; the voids list generates with citable evidence per row.

---

## Workstream M — the Master, proven

The skeleton is right (candidate blocks → Claude batch adjudication → human exception queue; SCD-2;
crosswalks; price/category corroboration). What's missing is **proof**. "We have a matching engine"
is good enough; "P=99.x% measured, every record carries confidence" is best.

**Scale (corrected 2026-07-24, see NRT-PLAN §10b):** intake is ~200–300M source item records
mastering to **800k–1.3M items** (~240:1 fan-in) — dominated by per-store repeats, so the collapse is
staged: SQL exact-UPC (eats the bulk) → deterministic composite keys → blocked similarity with
deterministic scorers → LLM only on the ambiguous ≤1–2M pairs → humans on thousands. Stages 0–2 are
warehouse-side SQL pushdown; the hard-tail identity engine is hoodie-canon's charter, fed ~5–15M
collapsed clusters, never 300M raws. Today's Python-loop `build_product_master.py` is explicitly
below this scale — do not grow it toward 200M. **Added scoreboard rows:** cold full re-master ≤ one
weekend on one worker; daily incremental master cycle ≤ 10 min. Gold-set strata (M1) must include
fan-in bands (2-source items vs 200-source items fail differently).

### M1. Gold set
- ~1,500 labeled pairs, stratified by category × source-pair × match-rule (UPC, name+size,
  price-corroborated, identity_resolve merges), plus deliberate hard negatives (same brand adjacent
  sizes, cask/lot variants — the known traps).
- Labeling: Claude batch first pass → human confirm on disagreements (the existing exception-queue
  muscle). Stored as `gold_matches`, append-only, versioned. Every future steward decision appends —
  the gold set grows as a free byproduct of operating.

### M2. Metrics harness + CI gate
- `master_quality.py`: precision / recall / F1 — overall, per category, per rule — against the gold
  set. Runs in CI on any matcher/blocking change; a drop beyond threshold blocks the merge (same
  pattern as the warehouse-compat gate).

### M3. Per-record confidence
- Every dim_item/dim_sku row gets `match_confidence` derived from rule strength + corroboration
  count + price-coherence + category-tree agreement. Exposed in the workbench and API. Single-source
  no-candidate records stay explicitly `provisional` (existing doctrine, now quantified).

### M4. Steward flywheel
- Queue ordered by impact × uncertainty (high-velocity items with low match confidence first — note
  the deliberate coupling: Workstream V tells M where accuracy is *worth* buying).

**Exit criteria:** P ≥99% / R ≥95% at item grain on the gold set; 100% of records carry confidence;
CI gate live; queue ordered by impact×uncertainty.

---

## Workstream R — Representativeness (from "what we saw" to "what the market did")

The bridge from observation engine to market-truth engine. Never pretend a scrape is a census —
*measure* how far it is from one and project with stated uncertainty.

### R1. Coverage accounting
- Cells: market × channel (chain / independent / on-premise) × state. Observed outlets (dim_outlet
  ∩ observation sources) vs the universe (src_outlets ~101k+, census reference, place coverage).
  Coverage % per cell on the coverage map — including the ugly cells. Especially the ugly cells.

### R2. Post-stratification
- Weights per cell = universe / observed (v1), refined by chain-size strata later. Market metrics
  ship in two flavors, labeled: **OBSERVED** (deterministic, what we saw) and **PROJECTED**
  (weighted, with CI) — the DETERMINISTIC-vs-INFERENCE doctrine extended to statistics.

### R3. Uncertainty
- Bootstrap CIs over stores within cell. Below min-n, the cell is suppressed with the reason shown —
  a blank cell that says why beats a number that lies.

### R4. Anchor validation
- Projected market totals vs Iowa/control-state actuals (and SipSource later) → publish the error.
  This is the same calibration loop as V4, reused — one validation spine, two consumers.

**Exit criteria:** every market metric carries coverage% + CI; validated cells within ±10% of
anchors; zero unlabeled projections anywhere in the suite.

---

## Workstream S — Survivability (the moat holds without its builder)

### S1. Cold-start runbook — restore from a clean Mac ≤1h (creds, launchd, runner clone, FDA grant),
  **tested annually, not just written**.
### S2. Second runner — decision gate: a warm-spare Mac mini (~$600 one-time) or cloud-headful
  fallback for the mac-klass subset that tolerates it. Adopt when any hot source depends on the Mac.
### S3. Recipe-rot MTTR — health digest already detects breaks + fixtures catch parser drift; add
  MTTR tracking to the console and a ≤72h repair SLA. Rot is inevitable; unmeasured rot is optional.
### S4. Path hardening — move the working repo out of the iCloud Desktop path (kills the TCC and
  smart-apostrophe failure class permanently; the runner clone already proved the pattern).

---

## Sequencing

| Order | What | Why first | Rough size |
|---|---|---|---|
| 1 | **V1–V4** velocity engine + calibration | The crown jewel AND it produces the headline accuracy number; everything else cites it | ~2–3 sessions |
| 2 | **M1–M2** gold set + harness + CI gate | Parallel-friendly; velocity credibility needs item identity underneath | ~1–2 sessions |
| 3 | **V5–V6** movers/voids surfaces | Ships the salable product on top of calibrated velocity | ~1 session |
| 4 | **R1–R4** coverage + projection | Reuses V4's anchor spine; converts observation → market claims | ~1–2 sessions |
| 5 | **M3–M4** confidence + flywheel | Uses V's impact ranking | ~1 session |
| 6 | **S1–S4** survivability | Continuous; S4 whenever a quiet window allows | background |

**Decision gates (no new recurring spend without hitting one):**
- **Panel-data purchase** — only if V4 shows scraped velocity cannot reach ≤15% MAPE on anchored
  cells (i.e., buy data only when measurement proves we need it).
- **Second-runner hardware** — when any hot-tier source depends solely on the Mac.
- **Snowflake** — unchanged from NRT-PLAN §2 (partition pruning failing, concurrency, >1h builds).

**The endgame sentence** (what the scoreboard lets us say, with receipts): *store-level distribution
and velocity, hours old, accuracy measured against actual state sales data, on a master with proven
precision, at ~$50 a month — and every number on the screen can show you its evidence.*
