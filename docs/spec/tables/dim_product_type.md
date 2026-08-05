# `dim_product_type`

|  |  |
|---|---|
| Status | landed |
| Rows | 5 |
| Columns | 2 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/dim_product_type.parquet` |


## Columns

| column | type |
|---|---|
| `product_type_id` | `BIGINT` |
| `product_type` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:237` | `write_parquet` | flat (full overwrite) | no |
