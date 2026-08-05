# `signal_voids`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,235 |
| Columns | 8 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-velocity-signals` |
| URI | `s3://hoodie-suite-warehouse/warehouse/signal_voids.parquet` |


## Columns

| column | type |
|---|---|
| `brand` | `VARCHAR` |
| `oos_stores` | `BIGINT` |
| `stores_seen` | `BIGINT` |
| `oos_events` | `DECIMAL(38,0)` |
| `typ_units_per_store_wk` | `DOUBLE` |
| `est_recoverable_units_wk` | `DOUBLE` |
| `pct_stores_out` | `DOUBLE` |
| `confidence` | `DOUBLE` |
