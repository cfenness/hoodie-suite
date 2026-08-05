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

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `obs` | `DECIMAL(38,0)` |
| `cells` | `BIGINT` |
| `stores` | `BIGINT` |
| `first_date` | `VARCHAR` |
| `last_date` | `VARCHAR` |
| `qty_coverage` | `DOUBLE` |
| `distinct_qty_global` | `BIGINT` |
| `diffable_frac` | `DOUBLE` |
| `jitter_frac` | `DOUBLE` |
| `median_cadence_days` | `DOUBLE` |
| `has_counts` | `BOOLEAN` |
| `qual_tier` | `VARCHAR` |
