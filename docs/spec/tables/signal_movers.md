# `signal_movers`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,015 |
| Columns | 12 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-velocity-signals` |
| URI | `s3://hoodie-suite-warehouse/warehouse/signal_movers.parquet` |


## Columns

| column | type |
|---|---|
| `brand` | `VARCHAR` |
| `week` | `TIMESTAMP` |
| `prev_week` | `TIMESTAMP` |
| `matched_cells` | `BIGINT` |
| `units` | `DOUBLE` |
| `prev_units` | `DOUBLE` |
| `delta_units` | `DOUBLE` |
| `pct_change` | `DOUBLE` |
| `confidence` | `DOUBLE` |
| `partial` | `BOOLEAN` |
| `cur_obs_days` | `INTEGER` |
| `prev_obs_days` | `INTEGER` |
