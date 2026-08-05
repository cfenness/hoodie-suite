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

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `source_id` | `VARCHAR` |
| `hoodie_brand` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `name_key` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:224` | `write_parquet` | flat (full overwrite) | no |
