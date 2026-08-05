# `postmates_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 3,190 |
| Columns | 47 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | yes |
| Written by sources | `postmates-full`, `build-ue-catalog` |
| URI | `s3://hoodie-suite-warehouse/warehouse/postmates_products.parquet` |


## Columns

| column | type |
|---|---|
| `item_uuid` | `VARCHAR` |
| `product_uuid` | `VARCHAR` |
| `store_uuid` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `name` | `VARCHAR` |
| `section` | `VARCHAR` |
| `subsection` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `gtins` | `VARCHAR` |
| `price` | `DOUBLE` |
| `list_price` | `DOUBLE` |
| `on_promo` | `BOOLEAN` |
| `discount` | `DOUBLE` |
| `promo_text` | `VARCHAR` |
| `promo_tag` | `VARCHAR` |
| `promo_type` | `VARCHAR` |
| `promo_pct` | `DOUBLE` |
| `promo_flat` | `DOUBLE` |
| `promo_uuid` | `VARCHAR` |
| `in_stock` | `BOOLEAN` |
| `is_sold_out` | `BOOLEAN` |
| `suspend_reason` | `VARCHAR` |
| `suspend_until` | `VARCHAR` |
| `low_availability` | `VARCHAR` |
| `avail_state` | `VARCHAR` |
| `stock_label` | `VARCHAR` |
| `max_qty` | `BIGINT` |
| `min_qty` | `DOUBLE` |
| `increment_qty` | `DOUBLE` |
| `default_qty` | `BIGINT` |
| `sold_by` | `VARCHAR` |
| `priced_by` | `VARCHAR` |
| `is_alcohol` | `BOOLEAN` |
| `num_alcoholic` | `BIGINT` |
| `age_rule` | `VARCHAR` |
| `abv` | `DOUBLE` |
| `pack` | `BIGINT` |
| `item_size` | `VARCHAR` |
| `nutritional_info` | `VARCHAR` |
| `classifications` | `VARCHAR` |
| `dietary_labels` | `VARCHAR` |
| `endorsements` | `VARCHAR` |
| `description` | `VARCHAR` |
| `image` | `VARCHAR` |
| `image_count` | `BIGINT` |
| `zone` | `VARCHAR` |
| `raw_json` | `VARCHAR` |
