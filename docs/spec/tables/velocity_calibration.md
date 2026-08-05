# `velocity_calibration`

|  |  |
|---|---|
| Status | landed |
| Rows | 5 |
| Columns | 6 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-velocity-calibrate` |
| URI | `s3://hoodie-suite-warehouse/warehouse/velocity_calibration.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `kind` | `VARCHAR` | 100.0% |
| `anchor` | `VARCHAR` | 20.0% |
| `source` | `VARCHAR` | 80.0% |
| `coverage` | `BIGINT` | 100.0% |
| `metric` | `VARCHAR` | 100.0% |
| `value` | `DOUBLE` | 40.0% |

Fill measured over **full table** (5 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `velocity_calibrate.py:142` | `write_parquet` | flat (full overwrite) | no |
