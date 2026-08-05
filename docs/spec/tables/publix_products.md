# `publix_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 5,477 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `publix` |
| URI | `s3://hoodie-suite-warehouse/warehouse/publix_products.parquet` |


## Columns

| column | type |
|---|---|
| `name` | `VARCHAR` |
| `promo_type` | `VARCHAR` |
| `is_bogo` | `BOOLEAN` |
| `savings` | `DOUBLE` |
| `deal_text` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `store` | `VARCHAR` |
| `market` | `VARCHAR` |
| `source` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `publix.py:140` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
