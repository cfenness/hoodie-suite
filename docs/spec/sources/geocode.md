# Geocode (Census, $0) — `geocode`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `geocode` |
| Runs | `import geocode as m; m.run()` |
| Module | `unifyd/geocode.py` — 155 lines |
| Cadence | daily |
| Enabled | no — does not run on a cadence |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 16384 MB / 5400 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** automate lat/lng: free US Census batch-geocodes addressed-but-ungeocoded src_outlets → maps on the Coverage page; unmatched marked county_fips=00000 so they aren't retried. GEOCODE_LIMIT/run


## 2. Transport

| constant | value |
|---|---|
| `BATCH_URL` | `https://geocoding.geo.census.gov/geocoder/geographies/addressbatch` |


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


## 4. `geocode.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
geocode.py — batch-geocode outlet addresses via the free US Census geocoder.

No API key. Batches addresses to geocoding.geo.census.gov/geocoder/geographies/addressbatch and
returns lat/lng + 5-digit county FIPS (state+county) — which also FIXES the FL numeric-county gap
(FL 'Location County' is a code, not a name) and makes FL census-joinable like TX, in one pass.
~80% match rate on US street addresses; unmatched rows get blank coords (no map dot).

`geocode_outlets(header, rows)` → (new_header, new_rows) with latitude/longitude/county_fips
appended, making any outlet dataset map-ready (Coverage Map) + census-joinable. Runs on the Mac
or Fly (network-bound, low memory). stdlib + requests.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
