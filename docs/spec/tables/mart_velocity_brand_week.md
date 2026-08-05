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

| column | type |
|---|---|
| `brand` | `VARCHAR` |
| `week` | `TIMESTAMP` |
| `implied_units` | `DOUBLE` |
| `conf_wt_units_per_store` | `DOUBLE` |
| `cells` | `BIGINT` |
| `stores` | `BIGINT` |
| `restock_events` | `DECIMAL(38,0)` |
| `oos_events` | `DECIMAL(38,0)` |
| `avg_confidence` | `DOUBLE` |
