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

| column | type | filled |
|---|---|---|
| `merge_id` | `VARCHAR` | 100.0% |
| `item_key` | `VARCHAR` | 100.0% |
| `pack` | `VARCHAR` | **0.6%** |
| `brand` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `size_ml` | `BIGINT` | 50.6% |
| `n_skus` | `BIGINT` | 100.0% |
| `total_rows` | `BIGINT` | 100.0% |
| `distinct_upc` | `BIGINT` | 100.0% |
| `confidence` | `DOUBLE` | 100.0% |
| `reason` | `VARCHAR` | 100.0% |
| `members` | `VARCHAR` | 100.0% |
| `attrs` | `VARCHAR` | 100.0% |

Fill measured over **full table** (4,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `wb_views.py:104` | `write_parquet` | flat (full overwrite) | no |
