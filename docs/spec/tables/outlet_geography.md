# `outlet_geography`

|  |  |
|---|---|
| Status | landed |
| Rows | 888,469 |
| Columns | 14 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full rebuild, layout-preserving) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/outlet_geography.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `hoodie_outlet` | `VARCHAR` |
| `lat` | `DOUBLE` |
| `lng` | `DOUBLE` |
| `county_fips` | `VARCHAR` |
| `county_name` | `VARCHAR` |
| `state_fp` | `VARCHAR` |
| `cbsa_code` | `VARCHAR` |
| `cbsa_name` | `VARCHAR` |
| `cbsa_type` | `VARCHAR` |
| `zcta` | `VARCHAR` |
| `method` | `VARCHAR` |
| `resolved_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `geo_resolve.py:257` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
