# `account_logos`

|  |  |
|---|---|
| Status | landed |
| Rows | 78 |
| Columns | 5 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/account_logos.parquet` |


## Columns

| column | type |
|---|---|
| `account` | `VARCHAR` |
| `source_url` | `VARCHAR` |
| `storage_key` | `VARCHAR` |
| `bytes` | `BIGINT` |
| `run_id` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `menu_site.py:385` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
