# `dim_outlet`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,560,546 |
| Columns | 22 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-outlets` |
| URI | `s3://hoodie-suite-warehouse/warehouse/dim_outlet.parquet` |


## Columns

| column | type |
|---|---|
| `hoodie_outlet_id` | `VARCHAR` |
| `outlet_name` | `VARCHAR` |
| `address` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `zip` | `VARCHAR` |
| `lat` | `DOUBLE` |
| `lng` | `DOUBLE` |
| `county_fips` | `VARCHAR` |
| `phone` | `VARCHAR` |
| `chain` | `VARCHAR` |
| `is_chain` | `BOOLEAN` |
| `f_beer` | `BOOLEAN` |
| `f_wine` | `BOOLEAN` |
| `f_spirits` | `BOOLEAN` |
| `f_hemp` | `BOOLEAN` |
| `f_cannabis` | `BOOLEAN` |
| `f_rtd_spirits` | `BOOLEAN` |
| `sources` | `VARCHAR[]` |
| `source_count` | `BIGINT` |
| `record_count` | `BIGINT` |
| `vpid` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dim_outlet.py:124` | `write_parquet` | flat (full overwrite) | no |
