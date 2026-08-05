# `outlet_master`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,818,275 |
| Columns | 18 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `outlet-union` |
| URI | `s3://hoodie-suite-warehouse/warehouse/outlet_master.parquet` |


## Columns

| column | type |
|---|---|
| `outlet_id` | `VARCHAR` |
| `name` | `VARCHAR` |
| `street` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `phone` | `VARCHAR` |
| `lat` | `DOUBLE` |
| `lng` | `DOUBLE` |
| `sources` | `VARCHAR` |
| `source_count` | `BIGINT` |
| `doordash_id` | `VARCHAR` |
| `toast_guid` | `VARCHAR` |
| `ubereats_id` | `VARCHAR` |
| `doordash_menu_date` | `VARCHAR` |
| `toast_menu_date` | `VARCHAR` |
| `ubereats_menu_date` | `VARCHAR` |
| `freshest_source` | `VARCHAR` |
| `freshest_date` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `outlet_union.py:137` | `write_parquet` | flat (full overwrite) | no |
