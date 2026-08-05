# `velocity_calibration`

|  |  |
|---|---|
| Status | landed |
| Rows | 5 |
| Columns | 6 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-velocity-calibrate` |
| URI | `s3://hoodie-suite-warehouse/warehouse/velocity_calibration.parquet` |


## Columns

| column | type |
|---|---|
| `kind` | `VARCHAR` |
| `anchor` | `VARCHAR` |
| `source` | `VARCHAR` |
| `coverage` | `BIGINT` |
| `metric` | `VARCHAR` |
| `value` | `DOUBLE` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `velocity_calibrate.py:142` | `write_parquet` | flat (full overwrite) | no |
