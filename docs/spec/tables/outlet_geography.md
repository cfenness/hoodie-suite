# `outlet_geography`

|  |  |
|---|---|
| Status | landed |
| Rows | 888,469 |
| Columns | 14 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full rebuild, layout-preserving) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/outlet_geography.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `hoodie_outlet` | `VARCHAR` | 100.0% |
| `lat` | `DOUBLE` | 100.0% |
| `lng` | `DOUBLE` | 100.0% |
| `county_fips` | `VARCHAR` | 100.0% |
| `county_name` | `VARCHAR` | 100.0% |
| `state_fp` | `VARCHAR` | 100.0% |
| `cbsa_code` | `VARCHAR` | 96.1% |
| `cbsa_name` | `VARCHAR` | 96.1% |
| `cbsa_type` | `VARCHAR` | 96.1% |
| `zcta` | `VARCHAR` | 99.6% |
| `method` | `VARCHAR` | 100.0% |
| `resolved_at` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `geo_resolve.py:257` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
