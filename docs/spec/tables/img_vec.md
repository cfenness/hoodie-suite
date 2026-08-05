# `img_vec`

|  |  |
|---|---|
| Status | landed |
| Rows | 29,297 |
| Columns | 5 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/img_vec.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `sku` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `image` | `VARCHAR` |
| `vec` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `img_embed.py:120` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
