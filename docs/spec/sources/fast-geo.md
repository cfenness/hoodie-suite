# Fast geo (city centroid, $0) — `fast-geo`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `fast-geo` |
| Runs | `import city_centroid as m; m.run()` |
| Module | `unifyd/city_centroid.py` — 285 lines |
| Cadence | daily |
| Enabled | no — does not run on a cadence |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 16384 MB / 5400 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** THE FAST LAYER: instantly city-centroid every un-geocoded src_outlet that ships a city+state (DoorDash: all 587k) → geo_precision=city, maps on Coverage immediately; the exact crawl upgrades city→exact. No fetch. FAST_GEO_LIMIT/run


## 2. Transport

_No literal endpoint constant in `city_centroid.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `refresh_fast`, `warehouse`


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


## 4. `city_centroid.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
city_centroid.py — the FAST geo layer: place any outlet with a city+state at its city centroid, instantly.

The precise geocoders (Census address batch, aggregator page-fetch) are exact but slow and need a street
address or a fetchable store page. Most outlets land with only city+state (DoorDash: 100% city, 93% state) —
so this layer maps them the moment they arrive, $0, no fetch, from a static reference:

  city_centroids  <- the free US Census Gazetteer (places + county subdivisions), state|city -> lat/lng.

Every outlet then carries a `geo_precision`: 'city' (this layer, ~city-accurate dot), 'exact' (a real
geocode/page fetch), 'city_miss'/'agg_miss' (tried, nothing found — don't retry forever). The precise passes
only ever UPGRADE a 'city' row to 'exact'; they never re-touch an 'exact' one. This is the layer that makes
"every outlet maps on the Coverage page" true immediately, with the exact crawl sharpening it over time.

geo_enrich_rows() is the INGESTION hook — call it in any sitemap/refresh path right before the write so no
outlet is ever taken in unplaced. fast_geo_pass() is the batch/registry entry that drains the existing backlog.
Reference build: build_reference() (stdlib urllib, run once / refresh yearly).
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
