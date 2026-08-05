# `market_projection`

|  |  |
|---|---|
| Status | landed |
| Rows | 5,921 |
| Columns | 11 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-representativeness` |
| URI | `s3://hoodie-suite-warehouse/warehouse/market_projection.parquet` |


## Columns

| column | type |
|---|---|
| `state` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `universe_outlets` | `BIGINT` |
| `obs_stores` | `BIGINT` |
| `coverage` | `DOUBLE` |
| `observed_units` | `BIGINT` |
| `projected_units` | `INTEGER` |
| `ci_low` | `INTEGER` |
| `ci_high` | `INTEGER` |
| `ci_pct` | `INTEGER` |
| `projected_status` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `representativeness.py:112` | `write_parquet` | flat (full overwrite) | no |
