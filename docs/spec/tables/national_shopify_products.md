# `national_shopify_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,141 |
| Columns | 16 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `shopify` |
| URI | `s3://hoodie-suite-warehouse/warehouse/national_shopify_products.parquet` |


## Columns

| column | type |
|---|---|
| `store` | `VARCHAR` |
| `platform` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `price_value` | `DOUBLE` |
| `sku` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `size_opt` | `VARCHAR` |
| `item_code` | `VARCHAR` |
| `bev_category` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `container` | `VARCHAR` |
| `unit_size` | `DOUBLE` |
| `size_uom` | `VARCHAR` |
| `pack_count` | `BIGINT` |
| `total_size` | `DOUBLE` |
