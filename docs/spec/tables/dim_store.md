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

| column | type | filled |
|---|---|---|
| `store_key` | `VARCHAR` | 100.0% |
| `hoodie_store_id` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 99.9% |
| `store_name` | `VARCHAR` | 100.0% |
| `chain` | `VARCHAR` | 74.1% |
| `city` | `VARCHAR` | 74.2% |
| `state` | `VARCHAR` | 74.2% |
| `zip` | `VARCHAR` | 73.9% |
| `lat` | `VARCHAR` | **0.3%** |
| `lng` | `VARCHAR` | **0.3%** |

Fill measured over **full table** (1,573 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `facts.py:159` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
