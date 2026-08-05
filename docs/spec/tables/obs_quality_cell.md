# `obs_quality_cell`

|  |  |
|---|---|
| Status | landed |
| Rows | 8,999,359 |
| Columns | 15 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-obs-quality` |
| URI | `s3://hoodie-suite-warehouse/warehouse/obs_quality_cell.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `n_obs` | `BIGINT` |
| `n_days` | `BIGINT` |
| `first_date` | `VARCHAR` |
| `last_date` | `VARCHAR` |
| `n_qty` | `BIGINT` |
| `distinct_qty` | `BIGINT` |
| `qty_moves` | `DECIMAL(38,0)` |
| `price_moves` | `DECIMAL(38,0)` |
| `jitter_moves` | `DECIMAL(38,0)` |
| `cadence_days` | `DOUBLE` |
