# `ubereats_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,160,806 |
| Columns | 16 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | yes |
| Written by sources | `ubereats-full`, `build-ue-catalog` |
| URI | `s3://hoodie-suite-warehouse/warehouse/ubereats_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store_uuid` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `source` | `INTEGER` | **0%** ‹never populated› |
| `item_uuid` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `INTEGER` | **0%** ‹never populated› |
| `upc` | `VARCHAR` | 8.1% |
| `gtin` | `INTEGER` | **0%** ‹never populated› |
| `price` | `DOUBLE` | 100.0% |
| `list_price` | `DOUBLE` | **4.3%** |
| `promo` | `INTEGER` | **0%** ‹never populated› |
| `size` | `INTEGER` | **0%** ‹never populated› |
| `abv` | `DOUBLE` | **1.3%** |
| `in_stock` | `BOOLEAN` | 100.0% |
| `stock_label` | `VARCHAR` | 16.4% |
| `category` | `INTEGER` | **0%** ‹never populated› |

Fill measured over **first 400,000 rows** (400,000 rows).

> **6 columns never populated:** `source`, `brand`, `gtin`, `promo`, `size`, `category`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.
