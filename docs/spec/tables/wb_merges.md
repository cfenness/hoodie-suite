# `wb_merges`

|  |  |
|---|---|
| Status | landed |
| Rows | 4,000 |
| Columns | 13 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/wb_merges.parquet` |


## Columns

| column | type |
|---|---|
| `merge_id` | `VARCHAR` |
| `item_key` | `VARCHAR` |
| `pack` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `name` | `VARCHAR` |
| `size_ml` | `BIGINT` |
| `n_skus` | `BIGINT` |
| `total_rows` | `BIGINT` |
| `distinct_upc` | `BIGINT` |
| `confidence` | `DOUBLE` |
| `reason` | `VARCHAR` |
| `members` | `VARCHAR` |
| `attrs` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `wb_views.py:104` | `write_parquet` | flat (full overwrite) | no |
