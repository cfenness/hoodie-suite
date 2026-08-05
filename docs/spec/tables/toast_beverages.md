# `toast_beverages`

|  |  |
|---|---|
| Status | landed |
| Rows | 27,269 |
| Columns | 15 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `toast` |
| URI | `s3://hoodie-suite-warehouse/warehouse/toast_beverages.parquet` |


## Columns

| column | type |
|---|---|
| `store` | `VARCHAR` |
| `account` | `VARCHAR` |
| `name` | `VARCHAR` |
| `description` | `VARCHAR` |
| `price` | `DOUBLE` |
| `category` | `VARCHAR` |
| `is_alcoholic` | `BOOLEAN` |
| `root` | `VARCHAR` |
| `sub` | `VARCHAR` |
| `base_spirit` | `VARCHAR` |
| `beer_style` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `source` | `VARCHAR` |
| `price_basis` | `VARCHAR` |
| `captured` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `toast.py:205` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
