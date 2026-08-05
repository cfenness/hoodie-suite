# `kroger_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 36,812 |
| Columns | 20 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `kroger-api` |
| URI | `s3://hoodie-suite-warehouse/warehouse/kroger_products.parquet` |


## Columns

| column | type |
|---|---|
| `product_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `category` | `VARCHAR` |
| `size` | `VARCHAR` |
| `price` | `DOUBLE` |
| `promo` | `DOUBLE` |
| `on_promo` | `BOOLEAN` |
| `stock_level` | `VARCHAR` |
| `in_stock` | `BOOLEAN` |
| `image_url` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `raw_json` | `VARCHAR` |
| `location_id` | `VARCHAR` |
| `term` | `VARCHAR` |
| `run_id` | `VARCHAR` |
| `store` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `kroger_api.py:218` | `write_parquet` | flat (full overwrite) | no |
