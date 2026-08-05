# `offprem_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 516,629 |
| Columns | 36 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `offprem-census` |
| URI | `s3://hoodie-suite-warehouse/warehouse/offprem_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `base` | `VARCHAR` | 100.0% |
| `platform` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 80.9% |
| `price_value` | `DOUBLE` | 100.0% |
| `sku` | `VARCHAR` | 63.5% |
| `upc` | `VARCHAR` | 31.7% |
| `size_ml` | `BIGINT` | 6.5% |
| `bev_category` | `VARCHAR` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |
| `container` | `VARCHAR` | **4.7%** |
| `unit_size` | `DOUBLE` | 6.6% |
| `size_uom` | `VARCHAR` | 6.6% |
| `pack_count` | `BIGINT` | 100.0% |
| `total_size` | `DOUBLE` | 6.6% |
| `tags` | `VARCHAR` | 65.3% |
| `description` | `VARCHAR` | 70.0% |
| `item_code` | `VARCHAR` | 95.5% |
| `product_type` | `VARCHAR` | 70.0% |
| `compare_at_price` | `DOUBLE` | 17.7% |
| `grams` | `BIGINT` | 80.3% |
| `in_stock` | `BOOLEAN` | 89.5% |
| `image` | `VARCHAR` | 75.8% |
| `size_opt` | `VARCHAR` | 15.2% |
| `vintage_opt` | `VARCHAR` | **0.7%** |
| `abv` | `VARCHAR` | **0%** ‹never populated› |
| `vintage` | `VARCHAR` | **0%** ‹never populated› |
| `origin` | `VARCHAR` | **0%** ‹never populated› |
| `bottled_in` | `INTEGER` | **0%** ‹never populated› |
| `region` | `VARCHAR` | **0%** ‹never populated› |
| `sub_region` | `INTEGER` | **0%** ‹never populated› |
| `appellation` | `INTEGER` | **0%** ‹never populated› |
| `varietal` | `VARCHAR` | **0%** ‹never populated› |
| `raw_json` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

> **8 columns never populated:** `abv`, `vintage`, `origin`, `bottled_in`, `region`, `sub_region`, `appellation`, `varietal`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `off_premise.py:976` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
