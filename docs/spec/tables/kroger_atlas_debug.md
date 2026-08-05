# `kroger_atlas_debug`

|  |  |
|---|---|
| Status | landed |
| Rows | 1 |
| Columns | 7 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/kroger_atlas_debug.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `http` | `BIGINT` | 100.0% |
| `products` | `BIGINT` | 100.0% |
| `store` | `VARCHAR` | 100.0% |
| `fac` | `VARCHAR` | 100.0% |
| `cookie_len` | `BIGINT` | 100.0% |
| `err` | `VARCHAR` | 100.0% |
| `snippet` | `VARCHAR` | 100.0% |

Fill measured over **full table** (1 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `kroger_atlas.py:199` | `write_parquet` | flat (full overwrite) | no |
