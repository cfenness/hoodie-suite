# `src_brands`

|  |  |
|---|---|
| Status | landed |
| Rows | 262,191 |
| Columns | 5 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/src_brands.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `source_id` | `VARCHAR` | 89.8% |
| `hoodie_brand` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `name_key` | `VARCHAR` | 99.8% |

Fill measured over **full table** (262,191 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:224` | `write_parquet` | flat (full overwrite) | no |
