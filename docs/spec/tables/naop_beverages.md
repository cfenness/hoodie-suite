# `naop_beverages`

|  |  |
|---|---|
| Status | landed |
| Rows | 7,139 |
| Columns | 16 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `naop` |
| URI | `s3://hoodie-suite-warehouse/warehouse/naop_beverages.parquet` |


## Columns

| column | type |
|---|---|
| `store` | `VARCHAR` |
| `account` | `VARCHAR` |
| `cuisine` | `VARCHAR` |
| `cuisines` | `VARCHAR` |
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
| `is_hemp` | `BOOLEAN` |
| `run_id` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `doordash_naop.py:193` | `write_parquet` | flat (full overwrite) | no |
