# `menu_beverages`

|  |  |
|---|---|
| Status | landed |
| Rows | 468 |
| Columns | 14 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/menu_beverages.parquet` |


## Columns

| column | type |
|---|---|
| `account` | `VARCHAR` |
| `name` | `VARCHAR` |
| `description` | `VARCHAR` |
| `price` | `DOUBLE` |
| `price_basis` | `VARCHAR` |
| `category` | `VARCHAR` |
| `is_alcoholic` | `BOOLEAN` |
| `root` | `VARCHAR` |
| `sub` | `VARCHAR` |
| `base_spirit` | `VARCHAR` |
| `beer_style` | `VARCHAR` |
| `source` | `VARCHAR` |
| `source_url` | `VARCHAR` |
| `run_id` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `menu_site.py:381` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
