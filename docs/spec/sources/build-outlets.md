# Outlet shred → dim_outlet — `build-outlets`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-outlets` |
| Runs | `import normalize as m; m.build(catalog=False, outlets=True, facts=False)` |
| Module | `unifyd/normalize.py` — 777 lines |
| Cadence | every 6h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 16384 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** src_outlets re-shred + cross-source geo-match consolidation (1.76M-row whole-table merge peaks >8GB)


## 2. Transport

_No literal endpoint constant in `normalize.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `city_centroid`, `class_type`, `dim_outlet`, `hoodie_ids`, `observe`, `warehouse`


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


### `dim_outlet`

2,560,546 rows · 22 columns


| column | type | filled |
|---|---|---|
| `hoodie_outlet_id` | `VARCHAR` | 100.0% |
| `outlet_name` | `VARCHAR` | 100.0% |
| `address` | `VARCHAR` | 18.3% |
| `city` | `VARCHAR` | 45.0% |
| `state` | `VARCHAR` | 43.0% |
| `zip` | `VARCHAR` | 17.2% |
| `lat` | `DOUBLE` | 43.0% |
| `lng` | `DOUBLE` | 43.0% |
| `county_fips` | `VARCHAR` | **3.0%** |
| `phone` | `VARCHAR` | **0.1%** |
| `chain` | `VARCHAR` | 8.3% |
| `is_chain` | `BOOLEAN` | 100.0% |
| `f_beer` | `BOOLEAN` | 100.0% |
| `f_wine` | `BOOLEAN` | 100.0% |
| `f_spirits` | `BOOLEAN` | 100.0% |
| `f_hemp` | `BOOLEAN` | 100.0% |
| `f_cannabis` | `BOOLEAN` | 100.0% |
| `f_rtd_spirits` | `BOOLEAN` | 100.0% |
| `sources` | `VARCHAR[]` | 100.0% |
| `source_count` | `BIGINT` | 100.0% |
| `record_count` | `BIGINT` | 100.0% |
| `vpid` | `VARCHAR` | 13.9% |

Fill measured over **first 400,000 rows** (400,000 rows).

**Written by** `dim_outlet.py:124` (write_parquet)


## 4. `normalize.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
normalize.py — the inbound normalization spine: shred every source ROW into a record at each GRAIN it
provides, tagged with the source. One central shredder, re-runnable, one place to maintain.

  one Total Wine row (New Amsterdam Vodka 1.75L PET @ store 920, $19.99, in stock) fans out to:
    src_brands   total-wine · New Amsterdam
    src_products total-wine · New Amsterdam Vodka
    src_items    total-wine · … · 1.75L
    src_skus     total-wine · … · PET · UPC · pack
    src_outlets  total-wine · store 920 "Total Wine Millenia" · address/geo
    src_pricing  total-wine · store920 · sku · date · 19.99
    src_inventory total-wine · store920 · sku · date · in_stock/qty

A source emits only the grains it provides (TTB → brands+products; FL DBPR → outlets; ABC → all). Each src_
record carries BOTH the raw source keys (source, source_id, upc, store_id) AND the Hoodie ID mnemonic at its
grain — so the same real entity from different sources shares the code (corroboration + matching per grain,
without needing a SKU match). src_<grain> then feeds dim_<grain> via the mnemonic matcher.

    python normalize.py               # rebuild all src_ tables
    python normalize.py --catalog     # just the brand/product/item/sku grains
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
