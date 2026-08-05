# `walmart_runs`

|  |  |
|---|---|
| Status | landed |
| Rows | 15 |
| Columns | 9 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/walmart_runs.parquet` |


## Columns

| column | type |
|---|---|
| `run_id` | `VARCHAR` |
| `at` | `BIGINT` |
| `products` | `BIGINT` |
| `in_stock` | `BIGINT` |
| `concerns` | `BIGINT` |
| `high_concerns` | `BIGINT` |
| `status` | `VARCHAR` |
| `note` | `VARCHAR` |
| `warnings` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `walmart_api.py:163` | `write_parquet` | flat (full overwrite) | no |
