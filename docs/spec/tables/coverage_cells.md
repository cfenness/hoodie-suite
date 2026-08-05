# `coverage_cells`

|  |  |
|---|---|
| Status | landed |
| Rows | 1 |
| Columns | 5 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-representativeness` |
| URI | `s3://hoodie-suite-warehouse/warehouse/coverage_cells.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `state` | `VARCHAR` | 100.0% |
| `universe_outlets` | `BIGINT` | 100.0% |
| `observed_outlets` | `BIGINT` | 100.0% |
| `coverage` | `DOUBLE` | 100.0% |
| `brands_observed` | `BIGINT` | 100.0% |

Fill measured over **full table** (1 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `representativeness.py:135` | `write_parquet` | flat (full overwrite) | no |
