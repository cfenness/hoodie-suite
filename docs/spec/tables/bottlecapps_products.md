# `bottlecapps_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 227 |
| Columns | 19 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `bottlecapps` |
| URI | `s3://hoodie-suite-warehouse/warehouse/bottlecapps_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `pid` | `VARCHAR` | 100.0% |
| `url` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 100.0% |
| `sku` | `VARCHAR` | 100.0% |
| `gtin` | `VARCHAR` | 26.4% |
| `price` | `DOUBLE` | 100.0% |
| `currency` | `VARCHAR` | 100.0% |
| `size` | `VARCHAR` | 30.4% |
| `availability` | `VARCHAR` | 100.0% |
| `description` | `VARCHAR` | 92.5% |
| `image` | `VARCHAR` | 100.0% |
| `rating` | `VARCHAR` | 100.0% |
| `rating_count` | `BIGINT` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |
| `raw_json` | `VARCHAR` | 100.0% |

Fill measured over **full table** (227 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `bottlecapps.py:197` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
