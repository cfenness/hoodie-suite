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
| **S3** | Freeze the `item_identity` contract; export canon's authoritative identity to Tigris so unifyd's serving path can read it. | both | |
| **S4** | Cut velocity/dim_item/marts to read `item_identity` (canon supersedes unifyd `item_key`); `master_quality` scores the SERVED identity. | unifyd | |
| **S5** | Retire unifyd matching to the deterministic pre-filter (or drop it); canon authoritative for all identity. | both | strangler-fig complete |

## Prerequisites / open checks

- Canon needs its **PG16+pgvector dev DB** up to run migrations + the cascade (`CANON_DB__DSN`,
  default `postgresql+psycopg://localhost/canon`).
- **Canon is dev-scale (verified S2): 1,460 observations, specs-only, all resolved, 29 entities.** The
  head-to-head is proven on this sample; the **served** lift now hinges on loading the FULL observation
  universe into canon (the Phase-1/2 ingest at scale) — that is the real blocker to S3+ (the cutover),
  not the cascade or the scoring. Loading the universe is the next concrete step.
- The `gold_matches` schema is the shared asset — canon reads it as its eval + review seed; keep it
  the one truth.

**The one-line rule (now correctly placed):** *canon is the identity engine; unifyd serves the interim
answer and grades both on one gold set; one contract carries the verdict to the surfaces; the cutover
is a data change, not a second engine.*
