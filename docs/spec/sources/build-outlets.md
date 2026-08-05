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

1,916,357 rows · 28 columns


| column | type |
|---|---|
| `source` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `chain` | `VARCHAR` |
| `is_chain` | `BOOLEAN` |
| `f_beer` | `BOOLEAN` |
| `f_wine` | `BOOLEAN` |
| `f_spirits` | `BOOLEAN` |
| `f_hemp` | `BOOLEAN` |
| `f_cannabis` | `BOOLEAN` |
| `f_rtd_spirits` | `BOOLEAN` |
| `flag_basis` | `VARCHAR` |
| `license_conflict` | `BOOLEAN` |
| `address` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `zip` | `VARCHAR` |
| `lat` | `DOUBLE` |
| `lng` | `DOUBLE` |
| `phone` | `VARCHAR` |
| `addr_valid` | `BOOLEAN` |
| `hoodie_outlet` | `VARCHAR` |
| `name_key` | `VARCHAR` |
| `phone_norm` | `VARCHAR` |
| `addr_key` | `VARCHAR` |
| `geo_cell` | `VARCHAR` |
| `county_fips` | `VARCHAR` |
| `geo_precision` | `VARCHAR` |


**Written by** `aggregator_geo.py:93` (write_accumulate), `city_centroid.py:268` (write_accumulate), `geocode.py:134` (write_accumulate), `mappability.py:163` (write_accumulate), `normalize.py:651` (write_full_rebuild), `reconcile_ue_ids.py:45` (write_full_rebuild), `refresh_fast.py:60` (write_accumulate), `ue_sitemap.py:97` (write_accumulate)


### `dim_outlet`

2,560,546 rows · 22 columns


| column | type |
|---|---|
| `hoodie_outlet_id` | `VARCHAR` |
| `outlet_name` | `VARCHAR` |
| `address` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `zip` | `VARCHAR` |
| `lat` | `DOUBLE` |
| `lng` | `DOUBLE` |
| `county_fips` | `VARCHAR` |
| `phone` | `VARCHAR` |
| `chain` | `VARCHAR` |
| `is_chain` | `BOOLEAN` |
| `f_beer` | `BOOLEAN` |
| `f_wine` | `BOOLEAN` |
| `f_spirits` | `BOOLEAN` |
| `f_hemp` | `BOOLEAN` |
| `f_cannabis` | `BOOLEAN` |
| `f_rtd_spirits` | `BOOLEAN` |
| `sources` | `VARCHAR[]` |
| `source_count` | `BIGINT` |
| `record_count` | `BIGINT` |
| `vpid` | `VARCHAR` |


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
