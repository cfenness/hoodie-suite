# Geo pipeline (all layers) — `geo`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `geo` |
| Runs | `import geo_all as m; m.run()` |
| Module | `unifyd/geo_all.py` — 73 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 16384 MB / 21600 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** THE daily geo run: fast-geo → geocode → aggregator-geo IN SEQUENCE on one machine. They each rewrite the whole src_outlets table, so running them concurrently would clobber each other — this serializes them. The three stay registered (enabled=False) for targeted manual backfills.


## 2. Transport

_No literal endpoint constant in `geo_all.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `aggregator_geo`, `city_centroid`, `geocode`, `mappability`


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


## 4. `geo_all.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
geo_all.py — run the whole geo pipeline in ONE process, in order, so the layers never race.

fast-geo, geocode, and aggregator-geo each write_accumulate the WHOLE src_outlets table (read → merge →
rewrite). Run concurrently they clobber each other — the last writer wins and silently drops the others' work.
So the daily schedule runs this single source instead of the three separately; they execute back-to-back on
one machine, each seeing the prior layer's writes:

  1. fast-geo     — city-centroid every un-geocoded outlet with a city+state (instant, $0)
  2. geocode      — Census street-address batch, upgrades city→exact where an address exists ($0)
  3. aggregator   — UberEats/Postmates store-page fetch, exact lat/lng for the no-address sitemap universe ($0)

The three remain in the registry (enabled=False) so they can still be spawned individually for a targeted
backfill — just never two-at-once against src_outlets.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
