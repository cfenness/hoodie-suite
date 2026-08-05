# `doordash_products_full`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,304,054 |
| Columns | 19 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `doordash-full` |
| URI | `s3://hoodie-suite-warehouse/warehouse/doordash_products_full.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `name` | `VARCHAR` | 100.0% |
| `price` | `VARCHAR` | 100.0% |
| `image_url` | `VARCHAR` | 100.0% |
| `container` | `VARCHAR` | 11.9% |
| `unit_size` | `DOUBLE` | 27.8% |
| `size_uom` | `VARCHAR` | 27.8% |
| `pack_count` | `DOUBLE` | 100.0% |
| `total_size` | `DOUBLE` | 27.8% |
| `store` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `price_value` | `DOUBLE` | 93.2% |
| `source` | `VARCHAR` | 100.0% |
| `department` | `VARCHAR` | 100.0% |
| `is_alcoholic` | `BOOLEAN` | 100.0% |
| `bev_category` | `VARCHAR` | 100.0% |
| `beer_style` | `VARCHAR` | **2.6%** |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).