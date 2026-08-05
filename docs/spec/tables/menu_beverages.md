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

| column | type | filled |
|---|---|---|
| `account` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `description` | `VARCHAR` | 94.0% |
| `price` | `DOUBLE` | 69.9% |
| `price_basis` | `VARCHAR` | 100.0% |
| `category` | `VARCHAR` | 100.0% |
| `is_alcoholic` | `BOOLEAN` | 100.0% |
| `root` | `VARCHAR` | 21.4% |
| `sub` | `VARCHAR` | 53.6% |
| `base_spirit` | `VARCHAR` | 20.5% |
| `beer_style` | `VARCHAR` | 11.5% |
| `source` | `VARCHAR` | 100.0% |
| `source_url` | `VARCHAR` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |

Fill measured over **full table** (468 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `menu_site.py:381` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
