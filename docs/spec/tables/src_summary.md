# `src_summary`

|  |  |
|---|---|
| Status | landed |
| Rows | 5 |
| Columns | 5 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/src_summary.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `grain` | `VARCHAR` | 100.0% |
| `table` | `VARCHAR` | 100.0% |
| `records` | `BIGINT` | 100.0% |
| `entities` | `BIGINT` | 100.0% |
| `corroborated` | `BIGINT` | 100.0% |

Fill measured over **full table** (5 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:743` | `write_parquet` | flat (full overwrite) | no |
