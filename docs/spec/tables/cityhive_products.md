# `cityhive_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 799 |
| Columns | 14 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `cityhive` |
| URI | `s3://hoodie-suite-warehouse/warehouse/cityhive_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `base` | `VARCHAR` | 100.0% |
| `platform` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | **0%** ‹never populated› |
| `price_value` | `DOUBLE` | 89.1% |
| `sku` | `VARCHAR` | 89.1% |
| `upc` | `VARCHAR` | **0%** ‹never populated› |
| `size_ml` | `BIGINT` | 84.6% |
| `image` | `VARCHAR` | 93.1% |
| `option_id` | `VARCHAR` | 100.0% |
| `bev_category` | `VARCHAR` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |

Fill measured over **full table** (799 rows).

> **2 columns never populated:** `brand`, `upc`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `cityhive.py:147` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
