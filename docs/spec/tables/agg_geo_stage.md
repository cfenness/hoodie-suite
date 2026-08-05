# `agg_geo_stage`

|  |  |
|---|---|
| Status | landed |
| Rows | 409,882 |
| Columns | 29 |
| Storage | partitioned |
| Partitions | 12 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/agg_geo_stage/s05_b0001.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `chain` | `VARCHAR` |
| `is_chain` | `BOOLEAN` |
| `f_beer` | `BOOLEAN` |
| `f_wine` | `BOOLEAN` |
| `f_spirits` | `BOOLEAN` |
| `f_hemp` | `BOOLEAN` |
| `f_cannabis` | `BOOLEAN` |
| `f_rtd_spirits` | `BOOLEAN` |
| `flag_basis` | `VARCHAR` |
| `license_conflict` | `BOOLEAN` |
| `address` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `zip` | `VARCHAR` |
| `lat` | `INTEGER` |
| `lng` | `INTEGER` |
| `phone` | `VARCHAR` |
| `addr_valid` | `BOOLEAN` |
| `hoodie_outlet` | `VARCHAR` |
| `name_key` | `VARCHAR` |
| `phone_norm` | `VARCHAR` |
| `addr_key` | `VARCHAR` |
| `geo_cell` | `VARCHAR` |
| `county_fips` | `VARCHAR` |
| `geo_precision` | `VARCHAR` |
| `staged_at` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `aggregator_geo.py:66` | `write_partition` | partitioned (append-only parts) | no |
