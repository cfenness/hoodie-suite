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

| column | type |
|---|---|
| `store_id` | `VARCHAR` |
| `sku` | `VARCHAR` |
| `name` | `VARCHAR` |
| `price` | `DOUBLE` |
| `retail_price` | `DOUBLE` |
| `size` | `DOUBLE` |
| `uom` | `VARCHAR` |
| `country_of_origin` | `VARCHAR` |
| `in_stock` | `BOOLEAN` |
| `source` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `trader_joes.py:145` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
