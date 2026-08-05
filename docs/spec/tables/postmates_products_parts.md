# `postmates_products_parts`

|  |  |
|---|---|
| Status | landed |
| Rows | 15,227 |
| Columns | 21 |
| Storage | partitioned |
| Partitions | 6 |
| Schema drift | uniform in sample |
| Write mode | — |
| Declared in `table_spec.py` | yes |
| Written by sources | `postmates` |
| URI | `s3://hoodie-suite-warehouse/warehouse/postmates_products_parts/2026-08-03_s00_b0001.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store_uuid` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | **0%** ‹never populated› |
| `item_uuid` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | **0%** ‹never populated› |
| `upc` | `VARCHAR` | 5.3% |
| `gtin` | `VARCHAR` | **0%** ‹never populated› |
| `price` | `DOUBLE` | 100.0% |
| `list_price` | `DOUBLE` | **2.2%** |
| `promo` | `VARCHAR` | **0%** ‹never populated› |
| `size` | `VARCHAR` | **0%** ‹never populated› |
| `abv` | `DOUBLE` | **1.5%** |
| `in_stock` | `BOOLEAN` | 100.0% |
| `stock_label` | `VARCHAR` | 13.4% |
| `category` | `VARCHAR` | **0%** ‹never populated› |
| `section` | `VARCHAR` | 100.0% |
| `subsection` | `VARCHAR` | 100.0% |
| `section_name` | `VARCHAR` | 92.7% |
| `subsection_name` | `VARCHAR` | 6.3% |
| `category_path` | `VARCHAR` | 98.8% |

Fill measured over **newest 6 of 6 partitions** (15,227 rows).

> **6 columns never populated:** `source`, `brand`, `gtin`, `promo`, `size`, `category`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.
