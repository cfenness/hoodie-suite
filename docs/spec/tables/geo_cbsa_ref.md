# `geo_cbsa_ref`

|  |  |
|---|---|
| Status | landed |
| Rows | 935 |
| Columns | 3 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/geo_cbsa_ref.parquet` |


## Columns

| column | type |
|---|---|
| `cbsa_code` | `VARCHAR` |
| `cbsa_name` | `VARCHAR` |
| `cbsa_type` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `geo_resolve.py:118` | `write_parquet` | flat (full overwrite) | no |
