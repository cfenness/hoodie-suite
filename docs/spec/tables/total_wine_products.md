# `total_wine_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 9,113 |
| Columns | 17 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `total-wine` |
| URI | `s3://hoodie-suite-warehouse/warehouse/total_wine_products.parquet` |


## Columns

| column | type |
|---|---|
| `sku` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `name` | `VARCHAR` |
| `size` | `VARCHAR` |
| `price` | `DOUBLE` |
| `category` | `VARCHAR` |
| `description` | `VARCHAR` |
| `image` | `VARCHAR` |
| `url` | `VARCHAR` |
| `varietal` | `VARCHAR` |
| `origin` | `VARCHAR` |
| `region` | `VARCHAR` |
| `sub_region` | `VARCHAR` |
| `appellation` | `VARCHAR` |
| `style` | `VARCHAR` |
| `abv` | `VARCHAR` |
| `run_id` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `total_wine.py:202` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `total_wine_full.py:44` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `total_wine_inventory.py:269` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
