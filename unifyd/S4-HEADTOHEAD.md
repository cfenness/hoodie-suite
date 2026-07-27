# S4 head-to-head runbook — score the SERVED canon identity in prod

Goal: land canon's `item_identity` in the prod warehouse, then score it against unifyd's `item_key`
on the SAME gold, to decide whether to proceed to the serving overlay (S4 slice 2). No live surface
changes here — this only measures.

## Verified prod state (2026-07-26, via `flyctl ssh console -a hoodie-suite`)

| table | prod rows | role |
|---|---|---|
| `xwalk_source_sku` | 1,331,401 | unifyd's `(source,product_id)→item_key` (all sources, incl. UPC-less) |
| `gold_matches` | 2 versions | the deterministic gold both engines score on |
| `master_quality` | 2 runs | **item_key baseline: P=1.000 R=0.285 F1=0.444** over 8,000 pairs (108,432 resolvable) |
| `item_identity` | **0 (MISSING)** | canon's authoritative identity — **must be landed first** |

`score_canon()` needs only `gold_matches` (present) + `item_identity` (missing). It does NOT need
`retail_observations`. So the ONLY gate is landing `item_identity`.

**Coverage caveat (expected):** canon's export currently covers 9 retail sources / ~90k SKUs
(offprem, kroger, sevennow, meijer, specs, bottlecapps, ubereats, walmart, postmates). Unifyd's xwalk
covers 1.33M incl. TTB/distributor/UPC-less sources canon hasn't ingested yet. So the head-to-head is on
the COVERED intersection; `score_canon` reports `coverage` = the share of the 8,000 gold pairs canon can
resolve. Read recall AND coverage together.

## Credentials

The warehouse writes to Tigris when these are set (they are Fly secrets on `hoodie-suite` /
the scraper app — never commit them, never paste into chat):

    AWS_ACCESS_KEY_ID  AWS_SECRET_ACCESS_KEY  AWS_ENDPOINT_URL_S3  BUCKET_NAME

Absent → `warehouse.remote()` is False and everything reads/writes a local dir (safe, but not prod).

## Prerequisite: the two PRs

- **#592** (`unifyd/ingest_canon_identity.py`) — the ingest.
- **#596** (`master_quality.score_canon` + `--canon`) — the scorer.

Either merge both to `main` (the Fly path runs deployed code), or run the commands from these branches
locally with creds exported (the local path).

## Path A — local, with creds (fastest; canon export already lives on the Mac)

Canon is local-only, so its export starts on the Mac. Run all three from the repo root with the 4 creds
exported into the shell:

```bash
# 1. export canon's identity (in the hoodie-canon repo) — already produced at /tmp/canon_item_identity.jsonl
#    (re-run if canon changed:  PYTHONPATH=src .venv/bin/python -m index.export_identity /tmp/canon_item_identity.jsonl)

# 2. land it in prod Tigris  (writes item_identity.parquet; empty-guard protects a good table)
.venv/bin/python unifyd/ingest_canon_identity.py /tmp/canon_item_identity.jsonl

# 3. score the served canon identity on the same gold  (lands master_quality_canon; prints the head-to-head)
.venv/bin/python unifyd/master_quality.py --canon
```

## Path B — on Fly (creds already in-env; needs #592/#596 merged + the JSONL uploaded)

```bash
# upload the export to a machine, then:
flyctl ssh console -a hoodie-suite -C "python /app/unifyd/ingest_canon_identity.py /path/to/canon_item_identity.jsonl"
flyctl ssh console -a hoodie-suite -C "python /app/unifyd/master_quality.py --canon"
```

## Read the result

```bash
flyctl ssh console -a hoodie-suite -C "python -c \"import sys; sys.path.insert(0,'/app/unifyd'); import warehouse as w; \
ik=w.query('master_quality','SELECT precision,recall,f1 FROM t ORDER BY version DESC LIMIT 1')[0]; \
c=w.query('master_quality_canon','SELECT precision,recall,f1,coverage,n_pairs FROM t ORDER BY version DESC LIMIT 1')[0]; \
print('item_key ', ik); print('canon    ', c)\""
```

`master_quality.py --canon` also logs the one-liner:
`↳ head-to-head vs item_key: R 0.285→<canon> ...`

## Decision gate → slice 2

Proceed to the serving overlay (group corroboration / "sold at N retailers" by `canon_item_id` in
`server.py` + `wb_views.py`) **only if**:

- canon **recall > item_key's 0.285** on the covered pairs (expected: canon merges same-UPC → toward 1.0), AND
- canon **precision stays ≈ 1.0** (no cross-brand over-merge), AND
- **coverage** is high enough on the served sources to be worth switching (if coverage is low, expand
  canon's source ingest first — the overlay only helps where canon covers).

If the lift holds, slice 2 changes live numbers; until then, nothing user-facing moves.
