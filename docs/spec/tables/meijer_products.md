# `meijer_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,144 |
| Columns | 16 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `meijer` |
| URI | `s3://hoodie-suite-warehouse/warehouse/meijer_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `size` | `DOUBLE` | 100.0% |
| `uom` | `VARCHAR` | **0%** ‹never populated› |
| `base_price` | `DOUBLE` | 100.0% |
| `price` | `DOUBLE` | 100.0% |
| `promo_price` | `DOUBLE` | 94.3% |
| `on_sale` | `BOOLEAN` | 100.0% |
| `price_text` | `VARCHAR` | 100.0% |
| `savings` | `VARCHAR` | 94.3% |
| `promo` | `VARCHAR` | **0%** ‹never populated› |
| `stock_status` | `VARCHAR` | 100.0% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (2,144 rows).

> **2 columns never populated:** `uom`, `promo`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `meijer.py:151` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
