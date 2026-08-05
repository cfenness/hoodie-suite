# DoorDash retail — full catalog (national, all beverage alcohol) — `doordash-full`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `doordash-full` |
| Runs | `import doordash_chains as m; m.run()` |
| Module | `unifyd/doordash_chains.py` — 219 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | free |
| Memory / timeout | 4096 MB / 14400 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** RESUMABLE national sweep of the FULL doordash_stores sitemap universe (767k+) via doordash_full.py's category-tree walk — NO curated chain list (a prior version matched only ~15 hand-picked banners against ~25k of the 767k stores; removed as a self-imposed scope limit, not a real constraint — the sitemap carries no chain/vertical field, so a non-retail store just costs one wasted fetch before the tree walk empties out). Lands one unified doordash_products_full/doordash_outlets_full table with per-store real-name attribution, not a per-chain table. shard/nshard partitions the remaining stores for running multiple machines concurrently at this scale. DDFULL_BATCH caps stores per run (accumulate-merged, never overwrites a prior batch, covered check unions the new table with every legacy per-chain table so nothing already scraped gets redone) — no permanent cap, no silent coverage gap. $0 flat ISP pool (Bright Data retired for DoorDash 2026-07-24)


## 2. Transport

_No literal endpoint constant in `doordash_chains.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `blocks`, `city_centroid`, `doordash_full`, `pace`, `runlog`, `warehouse`


## 3. What it lands


### `doordash_full_runs`

6 rows · 8 columns


| column | type |
|---|---|
| `run_id` | `VARCHAR` |
| `ts` | `BIGINT` |
| `universe_total` | `BIGINT` |
| `covered_total` | `BIGINT` |
| `remaining_total` | `BIGINT` |
| `stores_landed_this_run` | `BIGINT` |
| `items_landed_this_run` | `BIGINT` |
| `duration_s` | `DOUBLE` |


**Written by** `doordash_chains.py:185` (write_accumulate)


### `doordash_products_full`

2,304,054 rows · 19 columns


| column | type |
|---|---|
| `name` | `VARCHAR` |
| `price` | `VARCHAR` |
| `image_url` | `VARCHAR` |
| `container` | `VARCHAR` |
| `unit_size` | `DOUBLE` |
| `size_uom` | `VARCHAR` |
| `pack_count` | `DOUBLE` |
| `total_size` | `DOUBLE` |
| `store` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `price_value` | `DOUBLE` |
| `source` | `VARCHAR` |
| `department` | `VARCHAR` |
| `is_alcoholic` | `BOOLEAN` |
| `bev_category` | `VARCHAR` |
| `beer_style` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `run_id` | `VARCHAR` |


### `doordash_outlets_full`

**Has never landed.** `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/doordash_outlets_full.parquet' in region 'auto' (HTTP 404 Not Found)`

This is a registered source whose table does not exist in the warehouse — it has never completed a successful run, or it writes under a different name than the registry declares.


## 4. `doordash_chains.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
doordash_chains.py — full national DoorDash retail sweep. NO curated chain list.

Previously this bucketed `doordash_stores` against a hardcoded ~15-name list (CVS, Walgreens, Circle K,
...) and only ever attempted those ~24,919 stores out of the 767,716-row sitemap universe. That was a
self-imposed scope limit, not a real constraint — DoorDash's own sitemap carries no chain/vertical field
at all (restaurant and retail store ids look identical), so the curated list was pure premature
optimization to avoid wasting a fetch on non-retail stores. Removed: this now attempts every store in
`doordash_stores`. A store with no alcohol category costs exactly ONE wasted fetch before
`doordash_full.full_catalog()`'s tree walk empties out and returns — the existing per-store cost
structure already makes this cheap; the curated list was solving a problem that barely existed.

Lands into ONE unified table (`doordash_products_full` / `doordash_outlets_full`) instead of one table
per chain — the per-row `source` field carries the REAL store name from the sitemap (best-effort, not a
curated match), so downstream consumers still get an attribution without any hardcoded list gating
coverage.

RESUMABLE BATCHES, not a one-shot capped run — read what's already landed (union of the new unified
table AND every legacy `<chain>_products_full` table from before this change, so nothing already
scraped gets silently redone), subtract from the full universe, take up to `batch` of what's left.
Repeated triggers converge toward full coverage.

    python doordash_chains.py                  # one batch of the full universe
    python doordash_chains.py --batch 500       # a smaller manual slice
    python doordash_chains.py --shard 0 --nshard 6   # 1/6 of the universe, for running N machines at once
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
