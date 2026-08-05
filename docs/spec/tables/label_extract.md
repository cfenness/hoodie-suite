# `label_extract`

|  |  |
|---|---|
| Status | **never landed** |
| Rows | — |
| Columns | — |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/label_extract.parquet` |


> The table does not exist in the warehouse: `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/label_extract.parquet' in region 'auto' (HTTP 404 Not Found)`


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `label_vision.py:207` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
