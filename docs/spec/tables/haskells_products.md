# `haskells_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 10,535 |
| Columns | 19 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `haskells` |
| URI | `s3://hoodie-suite-warehouse/warehouse/haskells_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `sku` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | **0%** ‹never populated› |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | **2.9%** |
| `category` | `VARCHAR` | **0%** ‹never populated› |
| `price` | `DOUBLE` | 100.0% |
| `retail_price` | `DOUBLE` | 100.0% |
| `on_sale` | `BOOLEAN` | 100.0% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `qty` | `BIGINT` | 100.0% |
| `url` | `VARCHAR` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `hemp_signal` | `VARCHAR` | **1.4%** |
| `image` | `VARCHAR` | 73.8% |
| `captured_at` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `raw_json` | `VARCHAR` | 100.0% |

Fill measured over **full table** (10,535 rows).

> **2 columns never populated:** `upc`, `category`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `haskells.py:167` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
