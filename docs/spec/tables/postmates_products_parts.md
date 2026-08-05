# `postmates_products_parts`

|  |  |
|---|---|
| Status | landed |
| Rows | 6,040 |
| Columns | 21 |
| Storage | partitioned |
| Partitions | 1 |
| Schema drift | uniform in sample |
| Write mode | — |
| Declared in `table_spec.py` | yes |
| Written by sources | `postmates` |
| URI | `s3://hoodie-suite-warehouse/warehouse/postmates_products_parts/2026-08-03_s00_b0001.parquet` |


## Columns

| column | type |
|---|---|
| `store_uuid` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `source` | `VARCHAR` |
| `item_uuid` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `gtin` | `VARCHAR` |
| `price` | `DOUBLE` |
| `list_price` | `DOUBLE` |
| `promo` | `VARCHAR` |
| `size` | `VARCHAR` |
| `abv` | `DOUBLE` |
| `in_stock` | `BOOLEAN` |
| `stock_label` | `VARCHAR` |
| `category` | `VARCHAR` |
| `section` | `VARCHAR` |
| `subsection` | `VARCHAR` |
| `section_name` | `VARCHAR` |
| `subsection_name` | `VARCHAR` |
| `category_path` | `VARCHAR` |
