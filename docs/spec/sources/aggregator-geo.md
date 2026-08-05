# Aggregator geo (page-fetch) — `aggregator-geo`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `aggregator-geo` |
| Runs | `import aggregator_geo as m; m.run()` |
| Module | `unifyd/aggregator_geo.py` — 252 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 16384 MB / 7200 s |
| Shards | 6 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** $0 page-fetch PRECISE geo for the ~790k no-address ubereats/postmates outlets (schema.org lat/lng → geo_precision=exact; empty pages marked agg_miss). Big crawl — chips away, AGG_GEO_LIMIT/run. (doordash is mapped by the city-centroid fast layer, not here.)


## 2. Transport

_No literal endpoint constant in `aggregator_geo.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `getstore`, `outlet_ident`, `refresh_fast`, `resi`, `warehouse`


## 3. What it lands


### `src_outlets`

1,916,357 rows · 29 columns


| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `chain` | `VARCHAR` | 8.6% |
| `is_chain` | `BOOLEAN` | 100.0% |
| `f_beer` | `BOOLEAN` | 100.0% |
| `f_wine` | `BOOLEAN` | 100.0% |
| `f_spirits` | `BOOLEAN` | 100.0% |
| `f_hemp` | `BOOLEAN` | 100.0% |
| `f_cannabis` | `BOOLEAN` | 100.0% |
| `f_rtd_spirits` | `BOOLEAN` | 100.0% |
| `flag_basis` | `VARCHAR` | 100.0% |
| `license_conflict` | `BOOLEAN` | 100.0% |
| `address` | `VARCHAR` | 29.0% |
| `city` | `VARCHAR` | 59.3% |
| `state` | `VARCHAR` | 57.0% |
| `zip` | `VARCHAR` | 27.6% |
| `lat` | `DOUBLE` | 55.8% |
| `lng` | `DOUBLE` | 55.8% |
| `phone` | `VARCHAR` | **0.1%** |
| `addr_valid` | `BOOLEAN` | 100.0% |
| `hoodie_outlet` | `VARCHAR` | 100.0% |
| `name_key` | `VARCHAR` | 99.9% |
| `phone_norm` | `VARCHAR` | **0.1%** |
| `addr_key` | `VARCHAR` | 24.8% |
| `geo_cell` | `VARCHAR` | 25.0% |
| `county_fips` | `VARCHAR` | **4.5%** |
| `geo_precision` | `VARCHAR` | 100.0% |
| `__b` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

**Written by** `aggregator_geo.py:93` (write_accumulate), `city_centroid.py:268` (write_accumulate), `geocode.py:134` (write_accumulate), `mappability.py:163` (write_accumulate), `normalize.py:651` (write_full_rebuild), `reconcile_ue_ids.py:45` (write_full_rebuild), `refresh_fast.py:60` (write_accumulate), `ue_sitemap.py:97` (write_accumulate)


## 4. `aggregator_geo.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
aggregator_geo.py — PRECISE geo for UberEats/Postmates outlets, whose only geo source is the store page.

UberEats/Postmates land from the sitemap with just name+slug — no city, no address, no coords — so the fast
city-centroid layer and the Census address geocoder can't touch them; the store PAGE is the only geo source.
This fetches it $0 (curl_cffi Safari + the ISP pool) and pulls PRECISE lat/lng (+ address) straight off the
schema.org block, stamping geo_precision='exact'. A fetched-but-empty page is stamped 'agg_miss' so it isn't
re-fetched forever — the pass drains the ~790k no-address UberEats/Postmates pool run over run. Bounded
(AGG_GEO_LIMIT) + concurrent; a large crawl that chips away. (DoorDash ships city+state, so it's mapped
instantly by the city-centroid fast layer instead — see city_centroid.py.)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
