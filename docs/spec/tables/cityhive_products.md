# `cityhive_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 799 |
| Columns | 14 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `cityhive` |
| URI | `s3://hoodie-suite-warehouse/warehouse/cityhive_products.parquet` |


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
| `image` | `VARCHAR` |
| `option_id` | `VARCHAR` |
| `bev_category` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `run_id` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `cityhive.py:147` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
