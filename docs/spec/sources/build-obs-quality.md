# Observation quality (velocity substrate) — `build-obs-quality`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-obs-quality` |
| Runs | `import obs_quality as m; m.build()` |
| Module | `unifyd/obs_quality.py` — 191 lines |
| Cadence | every 12h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/obs_quality_test.py` |


**Registry note.** per-source instrument card + per-(store,sku) cell quality/jitter over retail_observations


## 2. Transport

_No literal endpoint constant in `obs_quality.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `obs_quality_source`

22 rows · 13 columns


| column | type |
|---|---|
| `source` | `VARCHAR` |
| `obs` | `DECIMAL(38,0)` |
| `cells` | `BIGINT` |
| `stores` | `BIGINT` |
| `first_date` | `VARCHAR` |
| `last_date` | `VARCHAR` |
| `qty_coverage` | `DOUBLE` |
| `distinct_qty_global` | `BIGINT` |
| `diffable_frac` | `DOUBLE` |
| `jitter_frac` | `DOUBLE` |
| `median_cadence_days` | `DOUBLE` |
| `has_counts` | `BOOLEAN` |
| `qual_tier` | `VARCHAR` |


### `obs_quality_cell`

8,999,359 rows · 15 columns


| column | type |
|---|---|
| `source` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `n_obs` | `BIGINT` |
| `n_days` | `BIGINT` |
| `first_date` | `VARCHAR` |
| `last_date` | `VARCHAR` |
| `n_qty` | `BIGINT` |
| `distinct_qty` | `BIGINT` |
| `qty_moves` | `DECIMAL(38,0)` |
| `price_moves` | `DECIMAL(38,0)` |
| `jitter_moves` | `DECIMAL(38,0)` |
| `cadence_days` | `DOUBLE` |


## 4. `obs_quality.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
obs_quality.py — the OBSERVATION-QUALITY layer (MOAT-PLAN.md Workstream V1).

Before we trust the instrument, we measure it. Velocity (V2+) reads inventory deltas out of
`retail_observations`; the confidence on every velocity number traces back to HOW WELL each cell is
observed. This build produces that error model — two grains, both pure DuckDB aggregation (no Python
row loops — binny's alone is 7.3M obs), landed to the warehouse:

  obs_quality_source   one row per source — the INSTRUMENT card: cadence, coverage, and crucially
                       whether `qty` is a real unit count or a STATUS-BUCKET in disguise (a source
                       that only ever reports qty ∈ {0,1} or a dozen round numbers is not counting).
  obs_quality_cell     one row per (source, store_id, product_id) — the velocity substrate: how many
                       times seen, over how long, at what cadence, with a jitter fingerprint (count
                       wobble WITHOUT a price/promo change = shelf-count noise, not a sale).

Nothing here estimates a sale yet (that's V2). This is: "how good is each cell's data, and which
sources are lying about having counts." Every downstream confidence cites `qual_tier` / `noise` here.

    python obs_quality.py            # rebuild both tables from retail_observations
    python obs_quality.py --stats    # + print the instrument cards
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
