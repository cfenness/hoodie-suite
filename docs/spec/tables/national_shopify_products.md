# `national_shopify_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,141 |
| Columns | 16 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `shopify` |
| URI | `s3://hoodie-suite-warehouse/warehouse/national_shopify_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `platform` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `price_value` | `DOUBLE` | 100.0% |
| `sku` | `VARCHAR` | 90.5% |
| `upc` | `VARCHAR` | **0%** ‹never populated› |
| `size_opt` | `VARCHAR` | 20.3% |
| `item_code` | `VARCHAR` | 100.0% |
| `bev_category` | `VARCHAR` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `container` | `VARCHAR` | **0.5%** |
| `unit_size` | `DOUBLE` | **0.1%** |
| `size_uom` | `VARCHAR` | **0.1%** |
| `pack_count` | `BIGINT` | 100.0% |
| `total_size` | `DOUBLE` | **0.1%** |

Fill measured over **full table** (1,141 rows).

> **1 column never populated:** `upc`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.
