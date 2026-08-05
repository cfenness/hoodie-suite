# `naop_beverages`

|  |  |
|---|---|
| Status | landed |
| Rows | 7,139 |
| Columns | 16 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `naop` |
| URI | `s3://hoodie-suite-warehouse/warehouse/naop_beverages.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `account` | `VARCHAR` | 100.0% |
| `cuisine` | `VARCHAR` | 70.8% |
| `cuisines` | `VARCHAR` | 67.4% |
| `name` | `VARCHAR` | 100.0% |
| `description` | `VARCHAR` | 88.6% |
| `price` | `DOUBLE` | 96.6% |
| `price_basis` | `VARCHAR` | 100.0% |
| `category` | `VARCHAR` | 100.0% |
| `is_alcoholic` | `BOOLEAN` | 100.0% |
| `root` | `VARCHAR` | 26.2% |
| `sub` | `VARCHAR` | 44.2% |
| `base_spirit` | `VARCHAR` | 25.3% |
| `beer_style` | `VARCHAR` | 5.2% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |

Fill measured over **full table** (7,139 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `doordash_naop.py:193` | `write_parquet` | flat (full overwrite) | no |
