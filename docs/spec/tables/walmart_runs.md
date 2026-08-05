# `walmart_runs`

|  |  |
|---|---|
| Status | landed |
| Rows | 15 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/walmart_runs.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `run_id` | `VARCHAR` | 100.0% |
| `at` | `BIGINT` | 100.0% |
| `products` | `BIGINT` | 100.0% |
| `in_stock` | `BIGINT` | 100.0% |
| `concerns` | `BIGINT` | 100.0% |
| `high_concerns` | `BIGINT` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `note` | `VARCHAR` | 93.3% |
| `warnings` | `VARCHAR` | 100.0% |

Fill measured over **full table** (15 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `walmart_api.py:163` | `write_parquet` | flat (full overwrite) | no |
