# `doordash_stores`

|  |  |
|---|---|
| Status | landed |
| Rows | 773,357 |
| Columns | 7 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `doordash-sitemap`, `doordash-geo-tx` |
| URI | `s3://hoodie-suite-warehouse/warehouse/doordash_stores.parquet` |


## Columns

| column | type |
|---|---|
| `store_id` | `VARCHAR` |
| `name` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `url` | `VARCHAR` |
| `type` | `VARCHAR` |
| `source` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `doordash_discover.py:133` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `doordash_sitemap.py:142` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
