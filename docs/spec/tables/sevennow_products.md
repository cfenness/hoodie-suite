# `sevennow_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 5,304 |
| Columns | 29 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `sevennow` |
| URI | `s3://hoodie-suite-warehouse/warehouse/sevennow_products.parquet` |


## Columns

| column | type |
|---|---|
| `store_id` | `VARCHAR` |
| `store_city` | `VARCHAR` |
| `department` | `VARCHAR` |
| `department_id` | `VARCHAR` |
| `category` | `VARCHAR` |
| `subcategory` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `slin` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `size` | `VARCHAR` |
| `price` | `DOUBLE` |
| `original_price` | `INTEGER` |
| `available` | `BOOLEAN` |
| `available_quantity` | `BIGINT` |
| `store_quantity` | `BIGINT` |
| `age_restricted` | `BOOLEAN` |
| `is_hemp` | `BOOLEAN` |
| `hemp_signal` | `VARCHAR` |
| `on_promo` | `BOOLEAN` |
| `promo` | `VARCHAR` |
| `promo_desc` | `VARCHAR` |
| `promo_ends` | `VARCHAR` |
| `image` | `VARCHAR` |
| `long_desc` | `VARCHAR` |
| `captured_at` | `BIGINT` |
| `source` | `VARCHAR` |
| `raw_json` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `sevennow.py:224` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
