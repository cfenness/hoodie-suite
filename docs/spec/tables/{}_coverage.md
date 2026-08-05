# `{}_coverage`

|  |  |
|---|---|
| Status | **never landed** |
| Rows | — |
| Columns | — |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/{}_coverage.parquet` |


> The table does not exist in the warehouse: `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/{}_coverage.parquet' in region 'auto' (HTTP 404 Not Found)`


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `place_coverage.py:244` | `write_parquet` | flat (full overwrite) | no |
