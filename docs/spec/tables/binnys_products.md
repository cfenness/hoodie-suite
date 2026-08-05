# `binnys_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,534,862 |
| Columns | 29 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated), flat (full rebuild, layout-preserving) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `binnys` |
| URI | `s3://hoodie-suite-warehouse/warehouse/binnys_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `sku` | `VARCHAR` | 100.0% |
| `store` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 95.1% |
| `varietal` | `VARCHAR` | 88.6% |
| `region` | `VARCHAR` | 25.9% |
| `origin` | `VARCHAR` | 98.4% |
| `category` | `VARCHAR` | 100.0% |
| `department` | `VARCHAR` | 100.0% |
| `item_size` | `VARCHAR` | 100.0% |
| `unit_label` | `VARCHAR` | 100.0% |
| `case_pack` | `DOUBLE` | 100.0% |
| `proof` | `DOUBLE` | 64.1% |
| `abv` | `DOUBLE` | 64.1% |
| `thc_mg` | `INTEGER` | **0%** ‹never populated› |
| `cbd_mg` | `INTEGER` | **0%** ‹never populated› |
| `rating` | `DOUBLE` | **4.2%** |
| `reviews` | `DOUBLE` | **4.2%** |
| `discount_pct` | `DOUBLE` | 100.0% |
| `deal_of_week` | `BOOLEAN` | 100.0% |
| `is_sold_out` | `BOOLEAN` | 100.0% |
| `in_store_only` | `BOOLEAN` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `short_desc` | `VARCHAR` | 100.0% |
| `product_url` | `VARCHAR` | 100.0% |
| `image` | `VARCHAR` | 100.0% |
| `price` | `DOUBLE` | 94.9% |
| `qty` | `BIGINT` | 100.0% |
| `raw_json` | `VARCHAR` | **0%** ‹never populated› |

Fill measured over **first 400,000 rows** (400,000 rows).

> **3 columns never populated:** `thc_mg`, `cbd_mg`, `raw_json`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `binnys_scraper.py:281` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
| `binnys_scraper.py:283` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
