# `obs_metro_rollup`

|  |  |
|---|---|
| Status | landed |
| Rows | 3,323 |
| Columns | 16 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full rebuild, layout-preserving) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/obs_metro_rollup.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `cbsa_code` | `VARCHAR` | 100.0% |
| `cbsa_name` | `VARCHAR` | 100.0% |
| `zcta` | `VARCHAR` | 100.0% |
| `county_fips` | `VARCHAR` | 100.0% |
| `stores` | `BIGINT` | 100.0% |
| `sources` | `BIGINT` | 100.0% |
| `items_total` | `BIGINT` | 100.0% |
| `items_per_store` | `DOUBLE` | 100.0% |
| `obs_rows` | `BIGINT` | 100.0% |
| `price_p25` | `DOUBLE` | 100.0% |
| `price_median` | `DOUBLE` | 100.0% |
| `price_p75` | `DOUBLE` | 100.0% |
| `price_avg` | `DOUBLE` | 100.0% |
| `promo_share` | `DOUBLE` | 100.0% |
| `in_stock_share` | `DOUBLE` | 100.0% |
| `built_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (3,323 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `obs_rollup.py:242` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
