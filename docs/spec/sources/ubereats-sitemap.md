# UberEats store universe — `ubereats-sitemap`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `ubereats-sitemap` |
| Runs | `import ue_sitemap as m; m.pull('ubereats'); m.sitemap_to_src_outlets('ubereats')` |
| Module | `unifyd/ue_sitemap.py` — 108 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 8192 MB / 10800 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** $0 US UberEats universe from its gzipped sitemaps (~285k) → src_outlets (the coverage book). Canonical UberEats harvester (ubereats_sitemap.py archived). accumulate into 995k src_outlets → 8gb


## 2. Transport

| constant | value |
|---|---|
| `SM` | `https://www.ubereats.com/sitemap-store-771af823-%03d.xml.gz` |


**Depends on** `kroger_api`, `refresh_fast`, `resi`, `warehouse`


## 3. What it lands


### `ubereats_sitemap`

755,032 rows · 6 columns


| column | type | filled |
|---|---|---|
| `store_uuid` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `slug` | `VARCHAR` | 100.0% |
| `url` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

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


## 4. `ue_sitemap.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
ue_sitemap.py — capture the FULL UberEats US outlet universe from the store sitemaps (~285k US stores), not
just the ~45k that metro feeds surface. robots.txt lists 26 `sitemap-store-*.xml.gz`; every US store URL (no
country prefix) becomes an outlet (uuid + name from the slug). GEO is null here — the feed coverage crawl fills
lat/lng for the zones it visits; this is the complete ACCOUNT list (every merchant incl. grocery/chains/indies).
Lands <site>_sitemap (separate table — no race with the live coverage crawl). Merge into src_outlets via
sitemap_to_src_outlets(). Headless curl_cffi + residential proxy, ~3min.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
