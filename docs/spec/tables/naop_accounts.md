# `naop_accounts`

|  |  |
|---|---|
| Status | landed |
| Rows | 4,794 |
| Columns | 13 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `naop` |
| URI | `s3://hoodie-suite-warehouse/warehouse/naop_accounts.parquet` |


## Columns

| column | type |
|---|---|
| `store` | `VARCHAR` |
| `account` | `VARCHAR` |
| `clean_name` | `VARCHAR` |
| `street` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `phone` | `VARCHAR` |
| `cuisine` | `VARCHAR` |
| `cuisines` | `VARCHAR` |
| `cuisine_source` | `VARCHAR` |
| `serves_alcohol` | `BOOLEAN` |
| `n_beverages` | `BIGINT` |
| `run_id` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `doordash_naop.py:195` | `write_parquet` | flat (full overwrite) | no |
