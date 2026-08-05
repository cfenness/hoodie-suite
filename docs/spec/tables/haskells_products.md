# `haskells_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 10,535 |
| Columns | 19 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `haskells` |
| URI | `s3://hoodie-suite-warehouse/warehouse/haskells_products.parquet` |


## Columns

| column | type |
|---|---|
| `store` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `sku` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `category` | `VARCHAR` |
| `price` | `DOUBLE` |
| `retail_price` | `DOUBLE` |
| `on_sale` | `BOOLEAN` |
| `in_stock` | `BOOLEAN` |
| `qty` | `BIGINT` |
| `url` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `hemp_signal` | `VARCHAR` |
| `image` | `VARCHAR` |
| `captured_at` | `BIGINT` |
| `source` | `VARCHAR` |
| `raw_json` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `haskells.py:167` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
