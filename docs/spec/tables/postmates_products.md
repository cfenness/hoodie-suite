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

| column | type | filled |
|---|---|---|
| `item_uuid` | `VARCHAR` | 100.0% |
| `product_uuid` | `VARCHAR` | 74.7% |
| `store_uuid` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `section` | `VARCHAR` | 100.0% |
| `subsection` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 23.4% |
| `gtins` | `VARCHAR` | 23.4% |
| `price` | `DOUBLE` | 100.0% |
| `list_price` | `DOUBLE` | 36.4% |
| `on_promo` | `BOOLEAN` | 100.0% |
| `discount` | `DOUBLE` | 100.0% |
| `promo_text` | `VARCHAR` | 17.3% |
| `promo_tag` | `VARCHAR` | 17.3% |
| `promo_type` | `VARCHAR` | 17.6% |
| `promo_pct` | `DOUBLE` | **4.1%** |
| `promo_flat` | `DOUBLE` | **4.1%** |
| `promo_uuid` | `VARCHAR` | 17.6% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `is_sold_out` | `BOOLEAN` | 100.0% |
| `suspend_reason` | `VARCHAR` | 25.3% |
| `suspend_until` | `VARCHAR` | **0%** ‹never populated› |
| `low_availability` | `VARCHAR` | **0%** ‹never populated› |
| `avail_state` | `VARCHAR` | 74.7% |
| `stock_label` | `VARCHAR` | **2.2%** |
| `max_qty` | `BIGINT` | 100.0% |
| `min_qty` | `DOUBLE` | 100.0% |
| `increment_qty` | `DOUBLE` | 100.0% |
| `default_qty` | `BIGINT` | 100.0% |
| `sold_by` | `VARCHAR` | 100.0% |
| `priced_by` | `VARCHAR` | 100.0% |
| `is_alcohol` | `BOOLEAN` | 100.0% |
| `num_alcoholic` | `BIGINT` | 9.6% |
| `age_rule` | `VARCHAR` | **1.6%** |
| `abv` | `DOUBLE` | 6.8% |
| `pack` | `BIGINT` | 14.8% |
| `item_size` | `VARCHAR` | 72.4% |
| `nutritional_info` | `VARCHAR` | 33.7% |
| `classifications` | `VARCHAR` | **0%** ‹never populated› |
| `dietary_labels` | `VARCHAR` | 21.8% |
| `endorsements` | `VARCHAR` | 35.0% |
| `description` | `VARCHAR` | 25.3% |
| `image` | `VARCHAR` | 100.0% |
| `image_count` | `BIGINT` | 100.0% |
| `zone` | `VARCHAR` | 100.0% |
| `raw_json` | `VARCHAR` | **0%** ‹never populated› |

Fill measured over **full table** (3,190 rows).

> **4 columns never populated:** `suspend_until`, `low_availability`, `classifications`, `raw_json`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.
