# `obs_store_rollup`

|  |  |
|---|---|
| Status | landed |
| Rows | 215,203 |
| Columns | 20 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full rebuild, layout-preserving) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/obs_store_rollup.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `chain` | `VARCHAR` |
| `items` | `BIGINT` |
| `brands` | `BIGINT` |
| `obs_rows` | `BIGINT` |
| `stores_seen_days` | `BIGINT` |
| `first_date` | `VARCHAR` |
| `last_date` | `VARCHAR` |
| `price_min` | `DOUBLE` |
| `price_p25` | `DOUBLE` |
| `price_median` | `DOUBLE` |
| `price_p75` | `DOUBLE` |
| `price_max` | `DOUBLE` |
| `price_avg` | `DOUBLE` |
| `promo_rows` | `BIGINT` |
| `promo_share` | `DOUBLE` |
| `in_stock_share` | `DOUBLE` |
| `hemp_items` | `BIGINT` |
| `built_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `obs_rollup.py:239` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
