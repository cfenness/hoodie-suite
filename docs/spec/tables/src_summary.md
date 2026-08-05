# `src_summary`

|  |  |
|---|---|
| Status | landed |
| Rows | 5 |
| Columns | 5 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/src_summary.parquet` |


## Columns

| column | type |
|---|---|
| `grain` | `VARCHAR` |
| `table` | `VARCHAR` |
| `records` | `BIGINT` |
| `entities` | `BIGINT` |
| `corroborated` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:743` | `write_parquet` | flat (full overwrite) | no |
