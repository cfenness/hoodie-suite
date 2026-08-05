# `img_vec`

|  |  |
|---|---|
| Status | landed |
| Rows | 29,297 |
| Columns | 5 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/img_vec.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `sku` | `VARCHAR` | 99.7% |
| `upc` | `VARCHAR` | 16.4% |
| `image` | `VARCHAR` | 100.0% |
| `vec` | `VARCHAR` | 100.0% |

Fill measured over **full table** (29,297 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `img_embed.py:120` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
