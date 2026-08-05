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

| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `account` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `description` | `VARCHAR` | 22.6% |
| `price` | `DOUBLE` | 85.3% |
| `category` | `VARCHAR` | 100.0% |
| `is_alcoholic` | `BOOLEAN` | 100.0% |
| `root` | `VARCHAR` | 23.4% |
| `sub` | `VARCHAR` | 48.0% |
| `base_spirit` | `VARCHAR` | 22.6% |
| `beer_style` | `VARCHAR` | 7.8% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `price_basis` | `VARCHAR` | 100.0% |
| `captured` | `VARCHAR` | 100.0% |

Fill measured over **full table** (27,269 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `toast.py:205` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
