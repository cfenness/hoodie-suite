# `salsify_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 63,889 |
| Columns | 35 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `bbg`, `salsify` |
| URI | `s3://hoodie-suite-warehouse/warehouse/salsify_products.parquet` |


## Columns

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

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `salsify.py:858` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
