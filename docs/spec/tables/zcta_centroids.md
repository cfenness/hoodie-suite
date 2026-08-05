# `zcta_centroids`

|  |  |
|---|---|
| Status | landed |
| Rows | 33,791 |
| Columns | 3 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/zcta_centroids.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `zip` | `VARCHAR` | 100.0% |
| `lat` | `DOUBLE` | 100.0% |
| `lng` | `DOUBLE` | 100.0% |

Fill measured over **full table** (33,791 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `zcta.py:75` | `write_parquet` | flat (full overwrite) | no |
