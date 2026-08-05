# `menu_files`

|  |  |
|---|---|
| Status | landed |
| Rows | 316 |
| Columns | 6 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/menu_files.parquet` |


## Columns

| column | type |
|---|---|
| `account` | `VARCHAR` |
| `kind` | `VARCHAR` |
| `source_url` | `VARCHAR` |
| `storage_key` | `VARCHAR` |
| `bytes` | `BIGINT` |
| `run_id` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `menu_site.py:383` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
