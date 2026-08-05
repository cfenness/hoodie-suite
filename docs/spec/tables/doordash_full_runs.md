# `doordash_full_runs`

|  |  |
|---|---|
| Status | landed |
| Rows | 6 |
| Columns | 8 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `doordash-full` |
| URI | `s3://hoodie-suite-warehouse/warehouse/doordash_full_runs.parquet` |


## Columns

| column | type |
|---|---|
| `run_id` | `VARCHAR` |
| `ts` | `BIGINT` |
| `universe_total` | `BIGINT` |
| `covered_total` | `BIGINT` |
| `remaining_total` | `BIGINT` |
| `stores_landed_this_run` | `BIGINT` |
| `items_landed_this_run` | `BIGINT` |
| `duration_s` | `DOUBLE` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `doordash_chains.py:185` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
