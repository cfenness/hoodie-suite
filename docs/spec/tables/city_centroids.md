# `city_centroids`

|  |  |
|---|---|
| Status | landed |
| Rows | 68,747 |
| Columns | 5 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `city-centroid-build` |
| URI | `s3://hoodie-suite-warehouse/warehouse/city_centroids.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `state` | `VARCHAR` | 100.0% |
| `city` | `VARCHAR` | 100.0% |
| `lat` | `DOUBLE` | 100.0% |
| `lng` | `DOUBLE` | 100.0% |
| `kind` | `VARCHAR` | 100.0% |

Fill measured over **full table** (68,747 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `city_centroid.py:93` | `write_parquet` | flat (full overwrite) | no |
