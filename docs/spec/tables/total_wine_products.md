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

| column | type | filled |
|---|---|---|
| `sku` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `size` | `VARCHAR` | 99.8% |
| `price` | `DOUBLE` | 7.0% |
| `category` | `VARCHAR` | 99.9% |
| `description` | `VARCHAR` | **0.1%** |
| `image` | `VARCHAR` | 79.6% |
| `url` | `VARCHAR` | 79.6% |
| `varietal` | `VARCHAR` | 99.9% |
| `origin` | `VARCHAR` | 100.0% |
| `region` | `VARCHAR` | 72.4% |
| `sub_region` | `VARCHAR` | **0%** ‹never populated› |
| `appellation` | `VARCHAR` | **0%** ‹never populated› |
| `style` | `VARCHAR` | 100.0% |
| `abv` | `VARCHAR` | 18.9% |
| `run_id` | `VARCHAR` | **0.1%** |

Fill measured over **full table** (9,113 rows).

> **2 columns never populated:** `sub_region`, `appellation`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `total_wine.py:202` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `total_wine_full.py:44` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `total_wine_inventory.py:269` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
