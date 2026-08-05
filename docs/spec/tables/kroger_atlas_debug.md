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

| column | type |
|---|---|
| `http` | `BIGINT` |
| `products` | `BIGINT` |
| `store` | `VARCHAR` |
| `fac` | `VARCHAR` |
| `cookie_len` | `BIGINT` |
| `err` | `VARCHAR` |
| `snippet` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `kroger_atlas.py:199` | `write_parquet` | flat (full overwrite) | no |
