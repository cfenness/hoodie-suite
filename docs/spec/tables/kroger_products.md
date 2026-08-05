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

| column | type | filled |
|---|---|---|
| `product_id` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 99.8% |
| `product_name` | `VARCHAR` | 100.0% |
| `category` | `VARCHAR` | 100.0% |
| `size` | `VARCHAR` | 100.0% |
| `price` | `DOUBLE` | 90.0% |
| `promo` | `DOUBLE` | 60.2% |
| `on_promo` | `BOOLEAN` | 100.0% |
| `stock_level` | `VARCHAR` | 18.9% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `image_url` | `VARCHAR` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `raw_json` | `VARCHAR` | 100.0% |
| `location_id` | `VARCHAR` | 100.0% |
| `term` | `VARCHAR` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |
| `store` | `VARCHAR` | 100.0% |
| `city` | `VARCHAR` | 100.0% |
| `state` | `VARCHAR` | 100.0% |

Fill measured over **full table** (36,812 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `kroger_api.py:218` | `write_parquet` | flat (full overwrite) | no |
