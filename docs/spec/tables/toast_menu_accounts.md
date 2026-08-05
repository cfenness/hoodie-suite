# `toast_menu_accounts`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,059 |
| Columns | 13 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `toast` |
| URI | `s3://hoodie-suite-warehouse/warehouse/toast_menu_accounts.parquet` |


## Columns

| column | type |
|---|---|
| `guid` | `VARCHAR` |
| `account` | `VARCHAR` |
| `clean_name` | `VARCHAR` |
| `street` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `phone` | `VARCHAR` |
| `lat` | `DOUBLE` |
| `lng` | `DOUBLE` |
| `serves_alcohol` | `BOOLEAN` |
| `n_beverages` | `BIGINT` |
| `source` | `VARCHAR` |
| `captured` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `toast.py:207` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
