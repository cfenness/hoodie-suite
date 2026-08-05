# `menu_files`

|  |  |
|---|---|
| Status | landed |
| Rows | 316 |
| Columns | 6 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/menu_files.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `account` | `VARCHAR` | 100.0% |
| `kind` | `VARCHAR` | 100.0% |
| `source_url` | `VARCHAR` | 100.0% |
| `storage_key` | `VARCHAR` | 100.0% |
| `bytes` | `BIGINT` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |

Fill measured over **full table** (316 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `menu_site.py:383` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
