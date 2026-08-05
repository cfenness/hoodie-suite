# `meijer_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,144 |
| Columns | 16 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `meijer` |
| URI | `s3://hoodie-suite-warehouse/warehouse/meijer_products.parquet` |


## Columns

| column | type |
|---|---|
| `store_id` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `name` | `VARCHAR` |
| `size` | `DOUBLE` |
| `uom` | `VARCHAR` |
| `base_price` | `DOUBLE` |
| `price` | `DOUBLE` |
| `promo_price` | `DOUBLE` |
| `on_sale` | `BOOLEAN` |
| `price_text` | `VARCHAR` |
| `savings` | `VARCHAR` |
| `promo` | `VARCHAR` |
| `stock_status` | `VARCHAR` |
| `in_stock` | `BOOLEAN` |
| `source` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `meijer.py:151` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
