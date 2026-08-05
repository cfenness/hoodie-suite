# `coverage_log`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,003 |
| Columns | 7 |
| Storage | partitioned |
| Partitions | 2,003 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/coverage_log/1785922914231349176_762_102.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `table` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 32.5% |
| `ts` | `DOUBLE` | 100.0% |
| `run` | `VARCHAR` | 27.5% |
| `wrote_rows` | `BIGINT` | 100.0% |
| `wrote_items` | `BIGINT` | 100.0% |
| `wrote_stores` | `BIGINT` | 100.0% |

Fill measured over **newest 40 of 2003 partitions** (40 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `coverage.py:111` | `write_partition` | partitioned (append-only parts) | no |
