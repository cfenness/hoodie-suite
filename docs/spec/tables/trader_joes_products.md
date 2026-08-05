# `trader_joes_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 4,024 |
| Columns | 10 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `trader-joes` |
| URI | `s3://hoodie-suite-warehouse/warehouse/trader_joes_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store_id` | `VARCHAR` | 100.0% |
| `sku` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `price` | `DOUBLE` | 100.0% |
| `retail_price` | `DOUBLE` | 100.0% |
| `size` | `DOUBLE` | 100.0% |
| `uom` | `VARCHAR` | 100.0% |
| `country_of_origin` | `VARCHAR` | 93.0% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (4,024 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `trader_joes.py:145` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
