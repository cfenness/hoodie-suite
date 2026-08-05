# `hoodie_ids`

|  |  |
|---|---|
| Status | landed |
| Rows | 11,864,042 |
| Columns | 6 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/hoodie_ids.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `entity_type` | `VARCHAR` | 100.0% |
| `master_key` | `VARCHAR` | 100.0% |
| `parent_key` | `VARCHAR` | 21.4% |
| `hoodie_id` | `VARCHAR` | 100.0% |
| `first_seen` | `VARCHAR` | 100.0% |
| `last_seen` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `hoodie_ids.py:184` | `write_parquet` | flat (full overwrite) | no |
