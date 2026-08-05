# `walmart_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 7,324 |
| Columns | 36 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `walmart` |
| URI | `s3://hoodie-suite-warehouse/warehouse/walmart_products.parquet` |


## Columns

| column | type |
|---|---|
| `product_name` | `VARCHAR` |
| `item_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `offer_id` | `VARCHAR` |
| `price` | `DOUBLE` |
| `size_ml` | `DOUBLE` |
| `brand` | `VARCHAR` |
| `type` | `VARCHAR` |
| `image` | `VARCHAR` |
| `url` | `VARCHAR` |
| `category` | `VARCHAR` |
| `category_path` | `VARCHAR` |
| `rh_path` | `VARCHAR` |
| `product_type_id` | `VARCHAR` |
| `primary_shelf_id` | `VARCHAR` |
| `ironbank_category` | `VARCHAR` |
| `is_alcohol` | `BOOLEAN` |
| `varietal` | `VARCHAR` |
| `region` | `VARCHAR` |
| `vintage` | `VARCHAR` |
| `abv` | `DOUBLE` |
| `container` | `VARCHAR` |
| `flavor` | `VARCHAR` |
| `pairing` | `VARCHAR` |
| `wine_score` | `VARCHAR` |
| `aisle` | `VARCHAR` |
| `order_limit` | `BIGINT` |
| `store_id` | `VARCHAR` |
| `store_state` | `VARCHAR` |
| `store_city` | `VARCHAR` |
| `avg_rating` | `DOUBLE` |
| `num_reviews` | `BIGINT` |
| `rollback` | `BOOLEAN` |
| `seller` | `VARCHAR` |
| `in_stock` | `BOOLEAN` |
| `raw_json` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `walmart_api.py:150` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `walmart_direct.py:300` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
