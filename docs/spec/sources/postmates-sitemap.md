# Postmates store universe — `postmates-sitemap`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `postmates-sitemap` |
| Runs | `import ue_sitemap as m; m.pull('postmates'); m.sitemap_to_src_outlets('postmates')` |
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


**Registry note.** $0 US Postmates universe from its sitemaps → src_outlets (coverage book)


## 2. Transport

| constant | value |
|---|---|
| `SM` | `https://www.ubereats.com/sitemap-store-771af823-%03d.xml.gz` |


**Depends on** `kroger_api`, `refresh_fast`, `resi`, `warehouse`


## 3. What it lands


### `postmates_sitemap`

269,007 rows · 6 columns


| column | type | filled |
|---|---|---|
| `store_uuid` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `slug` | `VARCHAR` | 100.0% |
| `url` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |

Fill measured over **full table** (269,007 rows).

### `src_outlets`

1,916,357 rows · 28 columns


| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | — |
| `store_id` | `VARCHAR` | — |
| `store_name` | `VARCHAR` | — |
| `chain` | `VARCHAR` | — |
| `is_chain` | `BOOLEAN` | — |
| `f_beer` | `BOOLEAN` | — |
| `f_wine` | `BOOLEAN` | — |
| `f_spirits` | `BOOLEAN` | — |
| `f_hemp` | `BOOLEAN` | — |
| `f_cannabis` | `BOOLEAN` | — |
| `f_rtd_spirits` | `BOOLEAN` | — |
| `flag_basis` | `VARCHAR` | — |
| `license_conflict` | `BOOLEAN` | — |
| `address` | `VARCHAR` | — |
| `city` | `VARCHAR` | — |
| `state` | `VARCHAR` | — |
| `zip` | `VARCHAR` | — |
| `lat` | `DOUBLE` | — |
| `lng` | `DOUBLE` | — |
| `phone` | `VARCHAR` | — |
| `addr_valid` | `BOOLEAN` | — |
| `hoodie_outlet` | `VARCHAR` | — |
| `name_key` | `VARCHAR` | — |
| `phone_norm` | `VARCHAR` | — |
| `addr_key` | `VARCHAR` | — |
| `geo_cell` | `VARCHAR` | — |
| `county_fips` | `VARCHAR` | — |
| `geo_precision` | `VARCHAR` | — |

_Fill rates not measured — rerun `spec_capture.py --fill`. Without them this is a list of columns, not a statement of what is captured._


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
