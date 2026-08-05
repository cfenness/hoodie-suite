# `doordash_products_full`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,304,054 |
| Columns | 19 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `doordash-full` |
| URI | `s3://hoodie-suite-warehouse/warehouse/doordash_products_full.parquet` |


## Columns

| column | type |
|---|---|
| `name` | `VARCHAR` |
| `price` | `VARCHAR` |
| `image_url` | `VARCHAR` |
| `container` | `VARCHAR` |
| `unit_size` | `DOUBLE` |
| `size_uom` | `VARCHAR` |
| `pack_count` | `DOUBLE` |
| `total_size` | `DOUBLE` |
| `store` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `price_value` | `DOUBLE` |
| `source` | `VARCHAR` |
| `department` | `VARCHAR` |
| `is_alcoholic` | `BOOLEAN` |
| `bev_category` | `VARCHAR` |
| `beer_style` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `run_id` | `VARCHAR` |
