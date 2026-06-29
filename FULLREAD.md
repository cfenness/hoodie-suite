# Full Read — build plan & status

Drop any data file → (1) deterministic R-console profile, (2) auto measure/dimension/
timeseries classification (user-overridable), (3) confident joins to existing datasets,
(4) a generated dashboard rendered from the real data. Intelligence = **Claude Opus 4.8**.
Full spec: `full-read-build-brief.md` (external).

## Governing principle (non-negotiable)
**Measured vs. inferred, never blended.**
- **Measured** — computed deterministically in-browser from the bytes (`fullread.js`). Never goes through the model.
- **Inferred** — Opus 4.8 reasoning over the *profile only*. Classifications, joins, narrative, dashboard composition. Hypotheses, always confidence-scored + user-overridable.
- Hard rules: (a) **raw rows never leave the browser**; only the profile (+ optional examples) is sent; (b) **the model never produces a displayed number** — it picks tiles/aggregations, the app computes every value; (c) **the deterministic layer works model-off**.

## Key reuse insight
"Model specifies tiles, app computes from real data" **is the Report Builder** (`rbOpenFromConfig`: measures × dimensions × viz → computed). So Full Read's `dashboard.tiles` → RB report specs, and `measure/dimension/timeseries` → RB measures/dimensions/time. The render-not-invent dashboard is mostly already built.

## Decisions locked (2026-06-29)
- **Privacy:** per-upload toggle — default profile + ~3 example values/column; switch to **stats-only** per file. Auto-withhold examples on account/PII-grain files.
- **Classification vocabulary:** full 5 — `measure | dimension | timeseries | identifier | ignore`.
- **Hoodie Intelligence:** both — open files (Full Read) AND freeform Q&A over loaded/estate data (render-not-invent).
- **Joins:** deferred — build phases 1–4 first; add join discovery once the Hoodie reference sets (item/outlet master) are registered with value-set indexes.
- **Charting:** reuse the Report Builder engine (vanilla, already render-not-invent). **Parsing:** PapaParse (CSV/TSV) + SheetJS (XLSX) loaded from CDN on demand (like the export libs); native JSON. **Proxy/key:** the existing `/api/analyze` (engine holds the key).

## Phases
1. **Deterministic profiler — DONE.** `fullread.js`: `profile(header, rows)` (dtypes, n/missing/distinct, numeric stats + outliers, categorical top-k, date min/max/granularity, candidate keys, dup rows, cell density, role_guess) + `ruleClassify(profile)` (rule-based default per the edge rules — the model-off fallback). Headless-tested.
2. **Re-architect `analyze`** → profile-only payload (no rows); Opus returns the brief's strict JSON (classifications + grain + joins + tile specs + narrative + questions), **no numbers**. Adopt the runtime system prompt + schema from the brief.
3. **UI — the editable Overlay dashboard.** Render like Hoodie Intelligence but as a **grid of editable tiles** (extend the My Dashboard grid: drag, flip, persist). Drop-in tile types: **graph · table · AI · writeup**. Console view (from the profile) + per-column classification override (override wins + persists). Dashboard renders via the Report Builder from the real uploaded data.
   - **Scoped Report Builder (the gating lift):** every graph/table tile opens RB restricted to **only the uploaded file's classified fields** (its columns → measures/dimensions/time) **plus** the joinable existing-data fields (phase 4). Requires generalizing RB — today fixed to the bev-alc cell model (`RB_FIELDS`, category×subch×division) — to treat an arbitrary uploaded dataset as a first-class field source and compute from it.
4. **Joins** vs a catalog (value-set / bloom-filter index) → the joinable fields that widen the scoped RB; re-introduce the **N≥10 grain guardrail** for transaction-grain Hoodie files.
5. **AI tiles + Hoodie Intelligence (2nd entry point, file open + Q&A).** AI tile populated two ways: (a) **suggested** — Claude proposes insights from the profile + domain knowledge of the data the user cares about; (b) **table-linked contextualizer** — link one or more tiles/tables to an AI tile; Claude reads the whole selection + underlying data and writes it up under a **strict causation-vs-correlation standard**: report associations by default, never assert causation without an identification strategy (experiment/quasi-experiment, temporal order + mechanism), and explicitly flag confounders / reverse causality / selection bias. Writeup tile = declarative narrative (Claude or hand-authored). All AI output stays render-not-invent (it cites the computed values, doesn't fabricate them).
