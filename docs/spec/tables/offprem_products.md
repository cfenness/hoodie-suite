# `offprem_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 516,629 |
| Columns | 36 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `offprem-census` |
| URI | `s3://hoodie-suite-warehouse/warehouse/offprem_products.parquet` |


## Columns

| column | type |
|---|---|
| `store` | `VARCHAR` |
| `base` | `VARCHAR` |
| `platform` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `price_value` | `DOUBLE` |
| `sku` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `size_ml` | `BIGINT` |
| `bev_category` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `run_id` | `VARCHAR` |
| `container` | `VARCHAR` |
| `unit_size` | `DOUBLE` |
| `size_uom` | `VARCHAR` |
| `pack_count` | `BIGINT` |
| `total_size` | `DOUBLE` |
| `tags` | `VARCHAR` |
| `description` | `VARCHAR` |
| `item_code` | `VARCHAR` |
| `product_type` | `VARCHAR` |
| `compare_at_price` | `DOUBLE` |
| `grams` | `BIGINT` |
| `in_stock` | `BOOLEAN` |
| `image` | `VARCHAR` |
| `size_opt` | `VARCHAR` |
| `vintage_opt` | `VARCHAR` |
| `abv` | `VARCHAR` |
| `vintage` | `VARCHAR` |
| `origin` | `VARCHAR` |
| `bottled_in` | `INTEGER` |
| `region` | `VARCHAR` |
| `sub_region` | `INTEGER` |
| `appellation` | `INTEGER` |
| `varietal` | `VARCHAR` |
| `raw_json` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `off_premise.py:976` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
