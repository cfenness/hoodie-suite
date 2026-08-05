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

| column | type |
|---|---|
| `ts` | `BIGINT` |
| `duration_s` | `DOUBLE` |
| `rows_total` | `BIGINT` |
| `tables` | `BIGINT` |
| `raw_tables` | `BIGINT` |
| `raw_rows` | `BIGINT` |
| `master_tables` | `BIGINT` |
| `master_rows` | `BIGINT` |
| `scope` | `VARCHAR` |
| `host` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `snowflake_load.py:53` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
