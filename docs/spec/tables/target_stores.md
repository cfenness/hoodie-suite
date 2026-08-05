# `target_stores`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,189 |
| Columns | 9 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `target` |
| URI | `s3://hoodie-suite-warehouse/warehouse/target_stores.parquet` |


## Columns

| column | type |
|---|---|
| `store_id` | `VARCHAR` |
| `name` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `zip` | `VARCHAR` |
| `address` | `VARCHAR` |
| `phone` | `VARCHAR` |
| `lat` | `INTEGER` |
| `lon` | `INTEGER` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `target_scraper.py:188` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
