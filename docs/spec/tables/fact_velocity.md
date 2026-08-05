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

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | **0.5%** |
| `brand` | `VARCHAR` | 92.3% |
| `week` | `TIMESTAMP` | 100.0% |
| `implied_units` | `DOUBLE` | 100.0% |
| `sale_events` | `DECIMAL(38,0)` | 100.0% |
| `restock_events` | `DECIMAL(38,0)` | 100.0% |
| `restock_units` | `DOUBLE` | 100.0% |
| `noise_damped_units` | `DOUBLE` | 100.0% |
| `censored_pairs` | `DECIMAL(38,0)` | 100.0% |
| `oos_events` | `DECIMAL(38,0)` | 100.0% |
| `pairs` | `DECIMAL(38,0)` | 100.0% |
| `jitter_frac` | `DOUBLE` | 100.0% |
| `cadence` | `DOUBLE` | 100.0% |
| `confidence` | `DOUBLE` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).