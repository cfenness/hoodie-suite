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

| column | type | filled |
|---|---|---|
| `product_name` | `VARCHAR` | 100.0% |
| `item_id` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 8.1% |
| `offer_id` | `VARCHAR` | 8.2% |
| `price` | `DOUBLE` | 65.4% |
| `size_ml` | `DOUBLE` | 59.1% |
| `brand` | `VARCHAR` | 8.2% |
| `type` | `VARCHAR` | 8.2% |
| `image` | `VARCHAR` | 100.0% |
| `url` | `VARCHAR` | 100.0% |
| `category` | `VARCHAR` | 58.3% |
| `category_path` | `VARCHAR` | 8.1% |
| `rh_path` | `VARCHAR` | 8.2% |
| `product_type_id` | `VARCHAR` | 8.2% |
| `primary_shelf_id` | `VARCHAR` | 8.2% |
| `ironbank_category` | `VARCHAR` | 8.2% |
| `is_alcohol` | `BOOLEAN` | 100.0% |
| `varietal` | `VARCHAR` | 7.6% |
| `region` | `VARCHAR` | 7.5% |
| `vintage` | `VARCHAR` | 5.9% |
| `abv` | `DOUBLE` | 7.3% |
| `container` | `VARCHAR` | 6.2% |
| `flavor` | `VARCHAR` | 7.7% |
| `pairing` | `VARCHAR` | 7.2% |
| `wine_score` | `VARCHAR` | **4.6%** |
| `aisle` | `VARCHAR` | **4.8%** |
| `order_limit` | `BIGINT` | 8.2% |
| `store_id` | `VARCHAR` | 8.2% |
| `store_state` | `VARCHAR` | 8.2% |
| `store_city` | `VARCHAR` | 8.2% |
| `avg_rating` | `DOUBLE` | 6.9% |
| `num_reviews` | `BIGINT` | 6.9% |
| `rollback` | `BOOLEAN` | 8.2% |
| `seller` | `VARCHAR` | 8.2% |
| `in_stock` | `BOOLEAN` | 8.2% |
| `raw_json` | `VARCHAR` | 8.2% |

Fill measured over **full table** (7,324 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `walmart_api.py:150` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `walmart_direct.py:300` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
