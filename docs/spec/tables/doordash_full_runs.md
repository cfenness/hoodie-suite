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

| column | type | filled |
|---|---|---|
| `run_id` | `VARCHAR` | 100.0% |
| `ts` | `BIGINT` | 100.0% |
| `universe_total` | `BIGINT` | 100.0% |
| `covered_total` | `BIGINT` | 100.0% |
| `remaining_total` | `BIGINT` | 100.0% |
| `stores_landed_this_run` | `BIGINT` | 100.0% |
| `items_landed_this_run` | `BIGINT` | 100.0% |
| `duration_s` | `DOUBLE` | 100.0% |

Fill measured over **full table** (6 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `doordash_chains.py:185` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
