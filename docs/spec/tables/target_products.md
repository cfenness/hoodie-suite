# `target_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,584 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `target` |
| URI | `s3://hoodie-suite-warehouse/warehouse/target_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `tcin` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 99.8% |
| `price` | `DOUBLE` | 99.9% |
| `promo` | `INTEGER` | **0%** ‹never populated› |
| `image_url` | `VARCHAR` | **0%** ‹never populated› |
| `category` | `VARCHAR` | **0%** ‹never populated› |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (1,584 rows).

> **3 columns never populated:** `promo`, `image_url`, `category`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `target_scraper.py:319` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `target_scraper.py:274` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
