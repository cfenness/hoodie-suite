# `retail_observations`

|  |  |
|---|---|
| Status | landed |
| Rows | 60,510,145 |
| Columns | 19 |
| Storage | partitioned |
| Partitions | 4,327 |
| Schema drift | **2 schemas in a 6-partition sample** |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | yes |
| Written by sources | `abc-fws` |
| URI | `s3://hoodie-suite-warehouse/warehouse/retail_observations/2026-08-05_publix.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `date` | `VARCHAR` | 100.0% |
| `observed_at` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `chain` | `VARCHAR` | **0%** ‹never populated› |
| `store` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | **0%** ‹never populated› |
| `gtin` | `VARCHAR` | **0%** ‹never populated› |
| `brand` | `VARCHAR` | 80.7% |
| `name` | `VARCHAR` | 100.0% |
| `price` | `DOUBLE` | 99.7% |
| `promo` | `DOUBLE` | **0%** ‹never populated› |
| `promo_text` | `VARCHAR` | **0%** ‹never populated› |
| `on_promo` | `BOOLEAN` | 100.0% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `qty` | `DOUBLE` | 81.1% |
| `stock_level` | `VARCHAR` | **0.2%** |
| `is_hemp` | `BOOLEAN` | 100.0% |

Fill measured over **newest 40 of 4327 partitions** (1,756,522 rows).

> **5 columns never populated:** `chain`, `upc`, `gtin`, `promo`, `promo_text`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `observe.py:156` | `write_partition` | partitioned (append-only parts) | yes |
