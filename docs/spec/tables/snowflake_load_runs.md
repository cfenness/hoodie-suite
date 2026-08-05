# `snowflake_load_runs`

|  |  |
|---|---|
| Status | landed |
| Rows | 4 |
| Columns | 10 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `snowflake-load` |
| URI | `s3://hoodie-suite-warehouse/warehouse/snowflake_load_runs.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `ts` | `BIGINT` | 100.0% |
| `duration_s` | `DOUBLE` | 100.0% |
| `rows_total` | `BIGINT` | 100.0% |
| `tables` | `BIGINT` | 100.0% |
| `raw_tables` | `BIGINT` | 100.0% |
| `raw_rows` | `BIGINT` | 100.0% |
| `master_tables` | `BIGINT` | 100.0% |
| `master_rows` | `BIGINT` | 100.0% |
| `scope` | `VARCHAR` | 100.0% |
| `host` | `VARCHAR` | 100.0% |

Fill measured over **full table** (4 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `snowflake_load.py:53` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
