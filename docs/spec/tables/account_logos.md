# `account_logos`

|  |  |
|---|---|
| Status | landed |
| Rows | 78 |
| Columns | 5 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/account_logos.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `account` | `VARCHAR` | 100.0% |
| `source_url` | `VARCHAR` | 100.0% |
| `storage_key` | `VARCHAR` | 100.0% |
| `bytes` | `BIGINT` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |

Fill measured over **full table** (78 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `menu_site.py:385` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
