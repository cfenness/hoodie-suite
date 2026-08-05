# `coverage_log`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,000 |
| Columns | 7 |
| Storage | partitioned |
| Partitions | 2,000 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/coverage_log/1785922914231349176_762_102.parquet` |


## Columns

| column | type |
|---|---|
| `table` | `VARCHAR` |
| `source` | `VARCHAR` |
| `ts` | `DOUBLE` |
| `run` | `VARCHAR` |
| `wrote_rows` | `BIGINT` |
| `wrote_items` | `BIGINT` |
| `wrote_stores` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `coverage.py:111` | `write_partition` | partitioned (append-only parts) | no |
