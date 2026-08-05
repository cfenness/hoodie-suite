# `dim_store`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,573 |
| Columns | 11 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/dim_store.parquet` |


## Columns

| column | type |
|---|---|
| `store_key` | `VARCHAR` |
| `hoodie_store_id` | `VARCHAR` |
| `source` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `chain` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `zip` | `VARCHAR` |
| `lat` | `VARCHAR` |
| `lng` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `facts.py:159` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
