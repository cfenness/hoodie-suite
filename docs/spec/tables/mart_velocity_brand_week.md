# `mart_velocity_brand_week`

|  |  |
|---|---|
| Status | landed |
| Rows | 13,282 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-velocity` |
| URI | `s3://hoodie-suite-warehouse/warehouse/mart_velocity_brand_week.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `brand` | `VARCHAR` | 100.0% |
| `week` | `TIMESTAMP` | 100.0% |
| `implied_units` | `DOUBLE` | 100.0% |
| `conf_wt_units_per_store` | `DOUBLE` | 100.0% |
| `cells` | `BIGINT` | 100.0% |
| `stores` | `BIGINT` | 100.0% |
| `restock_events` | `DECIMAL(38,0)` | 100.0% |
| `oos_events` | `DECIMAL(38,0)` | 100.0% |
| `avg_confidence` | `DOUBLE` | 100.0% |

Fill measured over **full table** (13,282 rows).