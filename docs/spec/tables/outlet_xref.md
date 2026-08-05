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

| column | type | filled |
|---|---|---|
| `obs_source` | `VARCHAR` | 100.0% |
| `obs_store_id` | `VARCHAR` | 100.0% |
| `geo_source` | `VARCHAR` | 100.0% |
| `geo_store_id` | `VARCHAR` | 100.0% |
| `hoodie_outlet` | `VARCHAR` | 100.0% |
| `cbsa_code` | `VARCHAR` | 99.7% |
| `cbsa_name` | `VARCHAR` | 99.7% |
| `zcta` | `VARCHAR` | 100.0% |
| `county_fips` | `VARCHAR` | 100.0% |
| `method` | `VARCHAR` | 100.0% |
| `confidence` | `DECIMAL(3,2)` | 100.0% |
| `built_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (9,567 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `outlet_xref.py:302` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
