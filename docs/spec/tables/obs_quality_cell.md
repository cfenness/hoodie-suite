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

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | **3.0%** |
| `brand` | `VARCHAR` | 17.4% |
| `n_obs` | `BIGINT` | 100.0% |
| `n_days` | `BIGINT` | 100.0% |
| `first_date` | `VARCHAR` | 100.0% |
| `last_date` | `VARCHAR` | 100.0% |
| `n_qty` | `BIGINT` | 100.0% |
| `distinct_qty` | `BIGINT` | 100.0% |
| `qty_moves` | `DECIMAL(38,0)` | 100.0% |
| `price_moves` | `DECIMAL(38,0)` | 100.0% |
| `jitter_moves` | `DECIMAL(38,0)` | 100.0% |
| `cadence_days` | `DOUBLE` | 22.8% |

Fill measured over **first 400,000 rows** (400,000 rows).