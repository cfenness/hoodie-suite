# `salsify_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 63,889 |
| Columns | 35 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `bbg`, `salsify` |
| URI | `s3://hoodie-suite-warehouse/warehouse/salsify_products.parquet` |


## Columns

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


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `salsify.py:858` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
