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

| column | type | filled |
|---|---|---|
| `store_id` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `city` | `VARCHAR` | 100.0% |
| `state` | `VARCHAR` | 99.9% |
| `url` | `VARCHAR` | 100.0% |
| `type` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `doordash_discover.py:133` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `doordash_sitemap.py:142` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
