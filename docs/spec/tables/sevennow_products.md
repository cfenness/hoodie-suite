# `sevennow_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 5,304 |
| Columns | 29 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `sevennow` |
| URI | `s3://hoodie-suite-warehouse/warehouse/sevennow_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store_id` | `VARCHAR` | 100.0% |
| `store_city` | `VARCHAR` | 100.0% |
| `department` | `VARCHAR` | 100.0% |
| `department_id` | `VARCHAR` | 100.0% |
| `category` | `VARCHAR` | 100.0% |
| `subcategory` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `slin` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | **0%** ‹never populated› |
| `size` | `VARCHAR` | 100.0% |
| `price` | `DOUBLE` | 100.0% |
| `original_price` | `INTEGER` | **0%** ‹never populated› |
| `available` | `BOOLEAN` | 100.0% |
| `available_quantity` | `BIGINT` | 100.0% |
| `store_quantity` | `BIGINT` | 100.0% |
| `age_restricted` | `BOOLEAN` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `hemp_signal` | `VARCHAR` | **0%** ‹never populated› |
| `on_promo` | `BOOLEAN` | 100.0% |
| `promo` | `VARCHAR` | 33.0% |
| `promo_desc` | `VARCHAR` | 30.2% |
| `promo_ends` | `VARCHAR` | 33.0% |
| `image` | `VARCHAR` | 100.0% |
| `long_desc` | `VARCHAR` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `raw_json` | `VARCHAR` | 100.0% |

Fill measured over **full table** (5,304 rows).

> **3 columns never populated:** `brand`, `original_price`, `hemp_signal`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `sevennow.py:224` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
