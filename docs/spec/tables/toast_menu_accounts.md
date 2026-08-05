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

| column | type | filled |
|---|---|---|
| `guid` | `VARCHAR` | 100.0% |
| `account` | `VARCHAR` | 100.0% |
| `clean_name` | `VARCHAR` | 99.6% |
| `street` | `VARCHAR` | 99.0% |
| `city` | `VARCHAR` | 99.0% |
| `state` | `VARCHAR` | 99.4% |
| `phone` | `VARCHAR` | 99.0% |
| `lat` | `DOUBLE` | 99.0% |
| `lng` | `DOUBLE` | 99.0% |
| `serves_alcohol` | `BOOLEAN` | 100.0% |
| `n_beverages` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `captured` | `VARCHAR` | 100.0% |

Fill measured over **full table** (2,059 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `toast.py:207` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
