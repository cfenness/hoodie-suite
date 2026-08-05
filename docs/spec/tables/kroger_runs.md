# `kroger_runs`

|  |  |
|---|---|
| Status | landed |
| Rows | 6 |
| Columns | 8 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/kroger_runs.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `run_id` | `VARCHAR` | 100.0% |
| `at` | `BIGINT` | 100.0% |
| `products` | `BIGINT` | 100.0% |
| `stores` | `BIGINT` | 100.0% |
| `in_stock` | `BIGINT` | 100.0% |
| `on_promo` | `BIGINT` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `note` | `VARCHAR` | 100.0% |

Fill measured over **full table** (6 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `kroger_api.py:233` | `write_parquet` | flat (full overwrite) | no |
