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

| column | type |
|---|---|
| `store_uuid` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `source` | `INTEGER` |
| `item_uuid` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `INTEGER` |
| `upc` | `VARCHAR` |
| `gtin` | `INTEGER` |
| `price` | `DOUBLE` |
| `list_price` | `DOUBLE` |
| `promo` | `INTEGER` |
| `size` | `INTEGER` |
| `abv` | `DOUBLE` |
| `in_stock` | `BOOLEAN` |
| `stock_label` | `VARCHAR` |
| `category` | `INTEGER` |
