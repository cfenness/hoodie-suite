# Salsify Sites (public catalog platform) — `salsify`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `salsify` |
| Runs | `import os, salsify as m; m.platform_pass(repair_properties=os.environ.get('SALSIFY_REPAIR') == '1')` |
| Module | `unifyd/os.py` |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 8192 MB / 21600 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** the LOOP: sites.salsify.com/sitemap_index.xml is a live directory of every PUBLIC catalog on the platform (519 sites / 118 orgs at 2026-08-03) — discover() lands the directory to salsify_catalogs, then EVERY seeded catalog (bbg, sazerac, heaven-hill) is pulled by this one process. Promote a discovered site by adding it to salsify.CATALOGS. Daily + resumable: each tick continues where the last stopped and only re-emits properties that MOVED


## 2. Transport

_No literal endpoint constant in `os.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


## 3. What it lands


### `salsify_catalogs`

520 rows · 16 columns


| column | type |
|---|---|
| `catalog_id` | `VARCHAR` |
| `org_id` | `VARCHAR` |
| `site_id` | `VARCHAR` |
| `catalog_name` | `VARCHAR` |
| `seeded` | `BOOLEAN` |
| `status` | `VARCHAR` |
| `total_products` | `BIGINT` |
| `total_pages` | `BIGINT` |
| `page_size` | `BIGINT` |
| `has_sitemap` | `BOOLEAN` |
| `allow_export` | `BOOLEAN` |
| `facet_properties` | `VARCHAR` |
| `publication_date` | `VARCHAR` |
| `build_id` | `VARCHAR` |
| `url` | `VARCHAR` |
| `checked_at` | `BIGINT` |


**Written by** `salsify.py:350` (write_accumulate), `salsify.py:767` (write_accumulate)


### `salsify_products`

63,889 rows · 35 columns


| column | type |
|---|---|
| `catalog_id` | `VARCHAR` |
| `catalog_name` | `VARCHAR` |
| `org_id` | `VARCHAR` |
| `site_id` | `VARCHAR` |
| `owner` | `VARCHAR` |
| `tier` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `dist_item_code` | `VARCHAR` |
| `system_id` | `VARCHAR` |
| `grouping_key` | `VARCHAR` |
| `sku_upc` | `VARCHAR` |
| `title` | `VARCHAR` |
| `item_description` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `sort_value` | `VARCHAR` |
| `supplier` | `VARCHAR` |
| `brand_owner` | `VARCHAR` |
| `category` | `VARCHAR` |
| `sub_category` | `VARCHAR` |
| `size_text` | `VARCHAR` |
| `size_ml` | `BIGINT` |
| `abv` | `DOUBLE` |
| `proof` | `DOUBLE` |
| `units_per_case` | `VARCHAR` |
| `country` | `VARCHAR` |
| `region` | `VARCHAR` |
| `varietal` | `VARCHAR` |
| `flavor` | `VARCHAR` |
| `market_region` | `VARCHAR` |
| `image` | `VARCHAR` |
| `image_count` | `BIGINT` |
| `property_count` | `BIGINT` |
| `properties_hash` | `VARCHAR` |
| `product_url` | `VARCHAR` |
| `pulled_at` | `VARCHAR` |


**Written by** `salsify.py:858` (write_accumulate)


### `salsify_properties`

2,870,998 rows · 10 columns · 314 partitions


| column | type |
|---|---|
| `catalog_id` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `group` | `VARCHAR` |
| `property` | `VARCHAR` |
| `label` | `VARCHAR` |
| `value_index` | `BIGINT` |
| `value` | `VARCHAR` |
| `asset_name` | `VARCHAR` |
| `day` | `VARCHAR` |
| `captured_at` | `BIGINT` |


**Written by** `salsify.py:873` (write_partition)


## 4. Module documentation

**`os.py` has no module docstring.** Everywhere else in this engine the docstring carries the rebuild narrative — the measurements behind the constants, the failure modes, the reason for the shape. Without it this source is only as legible as its code.


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
