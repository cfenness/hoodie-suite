# `obs_metro_rollup`

|  |  |
|---|---|
| Status | landed |
| Rows | 3,323 |
| Columns | 16 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full rebuild, layout-preserving) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/obs_metro_rollup.parquet` |


## Columns

| column | type |
|---|---|
| `cbsa_code` | `VARCHAR` |
| `cbsa_name` | `VARCHAR` |
| `zcta` | `VARCHAR` |
| `county_fips` | `VARCHAR` |
| `stores` | `BIGINT` |
| `sources` | `BIGINT` |
| `items_total` | `BIGINT` |
| `items_per_store` | `DOUBLE` |
| `obs_rows` | `BIGINT` |
| `price_p25` | `DOUBLE` |
| `price_median` | `DOUBLE` |
| `price_p75` | `DOUBLE` |
| `price_avg` | `DOUBLE` |
| `promo_share` | `DOUBLE` |
| `in_stock_share` | `DOUBLE` |
| `built_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `obs_rollup.py:242` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
