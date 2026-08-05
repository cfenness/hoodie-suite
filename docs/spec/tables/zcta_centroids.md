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

| column | type |
|---|---|
| `zip` | `VARCHAR` |
| `lat` | `DOUBLE` |
| `lng` | `DOUBLE` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `zcta.py:75` | `write_parquet` | flat (full overwrite) | no |
