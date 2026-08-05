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

| column | type | filled |
|---|---|---|
| `product_type_id` | `BIGINT` | 100.0% |
| `product_type` | `VARCHAR` | 100.0% |

Fill measured over **full table** (5 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:237` | `write_parquet` | flat (full overwrite) | no |
