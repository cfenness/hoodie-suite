# `bottlecapps_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 227 |
| Columns | 19 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `bottlecapps` |
| URI | `s3://hoodie-suite-warehouse/warehouse/bottlecapps_products.parquet` |


## Columns

| column | type |
|---|---|
| `store` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `pid` | `VARCHAR` |
| `url` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `sku` | `VARCHAR` |
| `gtin` | `VARCHAR` |
| `price` | `DOUBLE` |
| `currency` | `VARCHAR` |
| `size` | `VARCHAR` |
| `availability` | `VARCHAR` |
| `description` | `VARCHAR` |
| `image` | `VARCHAR` |
| `rating` | `VARCHAR` |
| `rating_count` | `BIGINT` |
| `captured_at` | `BIGINT` |
| `raw_json` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `bottlecapps.py:197` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
