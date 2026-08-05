# `ubereats_products_parts`

|  |  |
|---|---|
| Status | landed |
| Rows | 29,901,954 |
| Columns | 21 |
| Storage | partitioned |
| Partitions | 3,832 |
| Schema drift | uniform in sample |
| Write mode | — |
| Declared in `table_spec.py` | yes |
| Written by sources | `ubereats`, `ubereats-enrich` |
| URI | `s3://hoodie-suite-warehouse/warehouse/ubereats_products_parts/2026-08-04_s07_b0041.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store_uuid` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | **0%** ‹never populated› |
| `item_uuid` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | **0%** ‹never populated› |
| `upc` | `VARCHAR` | **0%** ‹never populated› |
| `gtin` | `VARCHAR` | **0%** ‹never populated› |
| `price` | `DOUBLE` | 100.0% |
| `list_price` | `DOUBLE` | **1.5%** |
| `promo` | `VARCHAR` | **0%** ‹never populated› |
| `size` | `VARCHAR` | **0%** ‹never populated› |
| `abv` | `DOUBLE` | **1.1%** |
| `in_stock` | `BOOLEAN` | 100.0% |
| `stock_label` | `VARCHAR` | **4.6%** |
| `category` | `VARCHAR` | **0%** ‹never populated› |
| `section` | `VARCHAR` | 100.0% |
| `subsection` | `VARCHAR` | 100.0% |
| `section_name` | `VARCHAR` | 90.5% |
| `subsection_name` | `VARCHAR` | 8.5% |
| `category_path` | `VARCHAR` | 98.4% |

Fill measured over **newest 40 of 3832 partitions** (323,569 rows).

> **7 columns never populated:** `source`, `brand`, `upc`, `gtin`, `promo`, `size`, `category`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.
