# `signal_voids`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,235 |
| Columns | 8 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-velocity-signals` |
| URI | `s3://hoodie-suite-warehouse/warehouse/signal_voids.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `brand` | `VARCHAR` | 100.0% |
| `oos_stores` | `BIGINT` | 100.0% |
| `stores_seen` | `BIGINT` | 100.0% |
| `oos_events` | `DECIMAL(38,0)` | 100.0% |
| `typ_units_per_store_wk` | `DOUBLE` | 100.0% |
| `est_recoverable_units_wk` | `DOUBLE` | 100.0% |
| `pct_stores_out` | `DOUBLE` | 100.0% |
| `confidence` | `DOUBLE` | 100.0% |

Fill measured over **full table** (2,235 rows).