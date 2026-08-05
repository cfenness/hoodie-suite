# `trade_area_demand`

|  |  |
|---|---|
| Status | landed |
| Rows | 36,092 |
| Columns | 12 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/trade_area_demand.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `geo_level` | `VARCHAR` | 100.0% |
| `geo_fips` | `VARCHAR` | 100.0% |
| `acs_vintage` | `BIGINT` | 100.0% |
| `cex_vintage` | `BIGINT` | 100.0% |
| `households` | `BIGINT` | 100.0% |
| `demand_total_usd` | `BIGINT` | 100.0% |
| `demand_at_home_usd` | `BIGINT` | 100.0% |
| `demand_away_usd` | `BIGINT` | 100.0% |
| `demand_per_hh_usd` | `DOUBLE` | 100.0% |
| `demand_index_vs_us` | `DOUBLE` | 100.0% |
| `method` | `VARCHAR` | 100.0% |
| `computed_at` | `BIGINT` | 100.0% |

Fill measured over **full table** (36,092 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `cex_ref.py:407` | `write_parquet` | flat (full overwrite) | no |
