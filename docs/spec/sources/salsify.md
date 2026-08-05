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


| column | type | filled |
|---|---|---|
| `catalog_id` | `VARCHAR` | 100.0% |
| `org_id` | `VARCHAR` | 100.0% |
| `site_id` | `VARCHAR` | 100.0% |
| `catalog_name` | `VARCHAR` | 97.7% |
| `seeded` | `BOOLEAN` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `total_products` | `BIGINT` | 97.7% |
| `total_pages` | `BIGINT` | 97.7% |
| `page_size` | `BIGINT` | 97.7% |
| `has_sitemap` | `BOOLEAN` | **0.6%** |
| `allow_export` | `BOOLEAN` | 97.7% |
| `facet_properties` | `VARCHAR` | 86.2% |
| `publication_date` | `VARCHAR` | 33.3% |
| `build_id` | `VARCHAR` | 97.7% |
| `url` | `VARCHAR` | 100.0% |
| `checked_at` | `BIGINT` | 100.0% |

Fill measured over **full table** (520 rows).

**Written by** `salsify.py:350` (write_accumulate), `salsify.py:767` (write_accumulate)


### `salsify_products`

63,889 rows · 35 columns


| column | type | filled |
|---|---|---|
| `catalog_id` | `VARCHAR` | 100.0% |
| `catalog_name` | `VARCHAR` | 100.0% |
| `org_id` | `VARCHAR` | 100.0% |
| `site_id` | `VARCHAR` | 100.0% |
| `owner` | `VARCHAR` | 100.0% |
| `tier` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `dist_item_code` | `VARCHAR` | 100.0% |
| `system_id` | `VARCHAR` | 100.0% |
| `grouping_key` | `VARCHAR` | 100.0% |
| `sku_upc` | `VARCHAR` | 16.6% |
| `title` | `VARCHAR` | 100.0% |
| `item_description` | `VARCHAR` | 50.8% |
| `brand` | `VARCHAR` | 100.0% |
| `sort_value` | `VARCHAR` | 100.0% |
| `supplier` | `VARCHAR` | 87.0% |
| `brand_owner` | `VARCHAR` | 12.0% |
| `category` | `VARCHAR` | 100.0% |
| `sub_category` | `VARCHAR` | 12.0% |
| `size_text` | `VARCHAR` | 100.0% |
| `size_ml` | `BIGINT` | 91.7% |
| `abv` | `DOUBLE` | 100.0% |
| `proof` | `DOUBLE` | 33.3% |
| `units_per_case` | `VARCHAR` | 87.0% |
| `country` | `VARCHAR` | 12.0% |
| `region` | `VARCHAR` | 67.4% |
| `varietal` | `VARCHAR` | 48.5% |
| `flavor` | `VARCHAR` | 87.0% |
| `market_region` | `VARCHAR` | 86.2% |
| `image` | `VARCHAR` | 83.8% |
| `image_count` | `BIGINT` | 100.0% |
| `property_count` | `BIGINT` | 100.0% |
| `properties_hash` | `VARCHAR` | 100.0% |
| `product_url` | `VARCHAR` | 100.0% |
| `pulled_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (63,889 rows).

**Written by** `salsify.py:858` (write_accumulate)


### `salsify_properties`

2,870,998 rows · 10 columns · 314 partitions


| column | type | filled |
|---|---|---|
| `catalog_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `group` | `VARCHAR` | 100.0% |
| `property` | `VARCHAR` | 100.0% |
| `label` | `VARCHAR` | 100.0% |
| `value_index` | `BIGINT` | 100.0% |
| `value` | `VARCHAR` | 100.0% |
| `asset_name` | `VARCHAR` | 6.4% |
| `day` | `VARCHAR` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |

Fill measured over **newest 40 of 314 partitions** (925,273 rows).

**Written by** `salsify.py:873` (write_partition)


## 4. Module documentation

**`os.py` has no module docstring.** Everywhere else in this engine the docstring carries the rebuild narrative — the measurements behind the constants, the failure modes, the reason for the shape. Without it this source is only as legible as its code.


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
