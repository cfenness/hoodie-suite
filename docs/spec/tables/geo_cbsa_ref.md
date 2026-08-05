# `geo_cbsa_ref`

|  |  |
|---|---|
| Status | landed |
| Rows | 935 |
| Columns | 3 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/geo_cbsa_ref.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `cbsa_code` | `VARCHAR` | 100.0% |
| `cbsa_name` | `VARCHAR` | 100.0% |
| `cbsa_type` | `VARCHAR` | 100.0% |

Fill measured over **full table** (935 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `geo_resolve.py:118` | `write_parquet` | flat (full overwrite) | no |
