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

| column | type | filled |
|---|---|---|
| `grain` | `VARCHAR` | 100.0% |
| `merge_id` | `VARCHAR` | 100.0% |
| `block` | `VARCHAR` | 100.0% |
| `canonical` | `VARCHAR` | 100.0% |
| `n` | `BIGINT` | 100.0% |
| `total_rows` | `BIGINT` | 100.0% |
| `confidence` | `DOUBLE` | 100.0% |
| `reason` | `VARCHAR` | 100.0% |
| `members` | `VARCHAR` | 100.0% |

Fill measured over **full table** (12,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `wb_views.py:196` | `write_parquet` | flat (full overwrite) | no |
