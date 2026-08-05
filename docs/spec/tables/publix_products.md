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

| column | type | filled |
|---|---|---|
| `name` | `VARCHAR` | 100.0% |
| `promo_type` | `VARCHAR` | 100.0% |
| `is_bogo` | `BOOLEAN` | 100.0% |
| `savings` | `DOUBLE` | 85.4% |
| `deal_text` | `VARCHAR` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `store` | `VARCHAR` | 100.0% |
| `market` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (5,477 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `publix.py:140` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
