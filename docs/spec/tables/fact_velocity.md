# `fact_velocity`

|  |  |
|---|---|
| Status | landed |
| Rows | 3,319,500 |
| Columns | 17 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-velocity` |
| URI | `s3://hoodie-suite-warehouse/warehouse/fact_velocity.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `week` | `TIMESTAMP` |
| `implied_units` | `DOUBLE` |
| `sale_events` | `DECIMAL(38,0)` |
| `restock_events` | `DECIMAL(38,0)` |
| `restock_units` | `DOUBLE` |
| `noise_damped_units` | `DOUBLE` |
| `censored_pairs` | `DECIMAL(38,0)` |
| `oos_events` | `DECIMAL(38,0)` |
| `pairs` | `DECIMAL(38,0)` |
| `jitter_frac` | `DOUBLE` |
| `cadence` | `DOUBLE` |
| `confidence` | `DOUBLE` |
