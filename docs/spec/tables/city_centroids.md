# `city_centroids`

|  |  |
|---|---|
| Status | landed |
| Rows | 68,747 |
| Columns | 5 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `city-centroid-build` |
| URI | `s3://hoodie-suite-warehouse/warehouse/city_centroids.parquet` |


## Columns

| column | type |
|---|---|
| `state` | `VARCHAR` |
| `city` | `VARCHAR` |
| `lat` | `DOUBLE` |
| `lng` | `DOUBLE` |
| `kind` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `city_centroid.py:93` | `write_parquet` | flat (full overwrite) | no |
