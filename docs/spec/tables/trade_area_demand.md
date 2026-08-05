# `trade_area_demand`

|  |  |
|---|---|
| Status | landed |
| Rows | 36,092 |
| Columns | 12 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/trade_area_demand.parquet` |


## Columns

| column | type |
|---|---|
| `geo_level` | `VARCHAR` |
| `geo_fips` | `VARCHAR` |
| `acs_vintage` | `BIGINT` |
| `cex_vintage` | `BIGINT` |
| `households` | `BIGINT` |
| `demand_total_usd` | `BIGINT` |
| `demand_at_home_usd` | `BIGINT` |
| `demand_away_usd` | `BIGINT` |
| `demand_per_hh_usd` | `DOUBLE` |
| `demand_index_vs_us` | `DOUBLE` |
| `method` | `VARCHAR` |
| `computed_at` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `cex_ref.py:407` | `write_parquet` | flat (full overwrite) | no |
