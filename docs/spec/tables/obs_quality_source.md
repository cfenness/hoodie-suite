# `obs_quality_source`

|  |  |
|---|---|
| Status | landed |
| Rows | 22 |
| Columns | 13 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-obs-quality` |
| URI | `s3://hoodie-suite-warehouse/warehouse/obs_quality_source.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `obs` | `DECIMAL(38,0)` | 100.0% |
| `cells` | `BIGINT` | 100.0% |
| `stores` | `BIGINT` | 100.0% |
| `first_date` | `VARCHAR` | 100.0% |
| `last_date` | `VARCHAR` | 100.0% |
| `qty_coverage` | `DOUBLE` | 100.0% |
| `distinct_qty_global` | `BIGINT` | 100.0% |
| `diffable_frac` | `DOUBLE` | 100.0% |
| `jitter_frac` | `DOUBLE` | 36.4% |
| `median_cadence_days` | `DOUBLE` | 77.3% |
| `has_counts` | `BOOLEAN` | 100.0% |
| `qual_tier` | `VARCHAR` | 100.0% |

Fill measured over **full table** (22 rows).