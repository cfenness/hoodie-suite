# `outlet_xref`

|  |  |
|---|---|
| Status | landed |
| Rows | 9,567 |
| Columns | 12 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full rebuild, layout-preserving) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/outlet_xref.parquet` |


## Columns

| column | type |
|---|---|
| `obs_source` | `VARCHAR` |
| `obs_store_id` | `VARCHAR` |
| `geo_source` | `VARCHAR` |
| `geo_store_id` | `VARCHAR` |
| `hoodie_outlet` | `VARCHAR` |
| `cbsa_code` | `VARCHAR` |
| `cbsa_name` | `VARCHAR` |
| `zcta` | `VARCHAR` |
| `county_fips` | `VARCHAR` |
| `method` | `VARCHAR` |
| `confidence` | `DECIMAL(3,2)` |
| `built_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `outlet_xref.py:302` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
