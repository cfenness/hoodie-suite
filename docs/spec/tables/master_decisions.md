# `master_decisions`

|  |  |
|---|---|
| Status | landed |
| Rows | 0 |
| Columns | 9 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/master_decisions.parquet` |


## Columns

| column | type |
|---|---|
| `cluster_id` | `INTEGER` |
| `action` | `INTEGER` |
| `tier` | `INTEGER` |
| `note` | `INTEGER` |
| `matched_name` | `INTEGER` |
| `steward` | `INTEGER` |
| `members` | `INTEGER` |
| `removed` | `INTEGER` |
| `ts` | `INTEGER` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `server.py:3543` | `write_parquet` | flat (full overwrite) | no |
| `server.py:3566` | `write_parquet` | flat (full overwrite) | no |
