# `wb_matches`

|  |  |
|---|---|
| Status | landed |
| Rows | 12,000 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/wb_matches.parquet` |


## Columns

| column | type |
|---|---|
| `grain` | `VARCHAR` |
| `merge_id` | `VARCHAR` |
| `block` | `VARCHAR` |
| `canonical` | `VARCHAR` |
| `n` | `BIGINT` |
| `total_rows` | `BIGINT` |
| `confidence` | `DOUBLE` |
| `reason` | `VARCHAR` |
| `members` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `wb_views.py:196` | `write_parquet` | flat (full overwrite) | no |
