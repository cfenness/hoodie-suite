# `dim_outlet`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,560,546 |
| Columns | 22 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-outlets` |
| URI | `s3://hoodie-suite-warehouse/warehouse/dim_outlet.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `hoodie_outlet_id` | `VARCHAR` | 100.0% |
| `outlet_name` | `VARCHAR` | 100.0% |
| `address` | `VARCHAR` | 18.3% |
| `city` | `VARCHAR` | 45.0% |
| `state` | `VARCHAR` | 43.0% |
| `zip` | `VARCHAR` | 17.2% |
| `lat` | `DOUBLE` | 43.0% |
| `lng` | `DOUBLE` | 43.0% |
| `county_fips` | `VARCHAR` | **3.0%** |
| `phone` | `VARCHAR` | **0.1%** |
| `chain` | `VARCHAR` | 8.3% |
| `is_chain` | `BOOLEAN` | 100.0% |
| `f_beer` | `BOOLEAN` | 100.0% |
| `f_wine` | `BOOLEAN` | 100.0% |
| `f_spirits` | `BOOLEAN` | 100.0% |
| `f_hemp` | `BOOLEAN` | 100.0% |
| `f_cannabis` | `BOOLEAN` | 100.0% |
| `f_rtd_spirits` | `BOOLEAN` | 100.0% |
| `sources` | `VARCHAR[]` | 100.0% |
| `source_count` | `BIGINT` | 100.0% |
| `record_count` | `BIGINT` | 100.0% |
| `vpid` | `VARCHAR` | 13.9% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dim_outlet.py:124` | `write_parquet` | flat (full overwrite) | no |
