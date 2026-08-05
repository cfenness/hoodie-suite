# `hoodie_ids`

|  |  |
|---|---|
| Status | landed |
| Rows | 11,864,042 |
| Columns | 6 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/hoodie_ids.parquet` |


## Columns

| column | type |
|---|---|
| `entity_type` | `VARCHAR` |
| `master_key` | `VARCHAR` |
| `parent_key` | `VARCHAR` |
| `hoodie_id` | `VARCHAR` |
| `first_seen` | `VARCHAR` |
| `last_seen` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `hoodie_ids.py:184` | `write_parquet` | flat (full overwrite) | no |
