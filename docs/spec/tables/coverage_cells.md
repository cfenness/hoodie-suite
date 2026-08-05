# `coverage_cells`

|  |  |
|---|---|
| Status | landed |
| Rows | 1 |
| Columns | 5 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-representativeness` |
| URI | `s3://hoodie-suite-warehouse/warehouse/coverage_cells.parquet` |


## Columns

| column | type |
|---|---|
| `state` | `VARCHAR` |
| `universe_outlets` | `BIGINT` |
| `observed_outlets` | `BIGINT` |
| `coverage` | `DOUBLE` |
| `brands_observed` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `representativeness.py:135` | `write_parquet` | flat (full overwrite) | no |
