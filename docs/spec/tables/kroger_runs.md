# `kroger_runs`

|  |  |
|---|---|
| Status | landed |
| Rows | 6 |
| Columns | 8 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/kroger_runs.parquet` |


## Columns

| column | type |
|---|---|
| `run_id` | `VARCHAR` |
| `at` | `BIGINT` |
| `products` | `BIGINT` |
| `stores` | `BIGINT` |
| `in_stock` | `BIGINT` |
| `on_promo` | `BIGINT` |
| `status` | `VARCHAR` |
| `note` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `kroger_api.py:233` | `write_parquet` | flat (full overwrite) | no |
