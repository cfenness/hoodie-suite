# `target_stores`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,189 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `target` |
| URI | `s3://hoodie-suite-warehouse/warehouse/target_stores.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store_id` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `city` | `VARCHAR` | 100.0% |
| `state` | `VARCHAR` | 100.0% |
| `zip` | `VARCHAR` | 100.0% |
| `address` | `VARCHAR` | 100.0% |
| `phone` | `VARCHAR` | 100.0% |
| `lat` | `INTEGER` | **0%** ‹never populated› |
| `lon` | `INTEGER` | **0%** ‹never populated› |

Fill measured over **full table** (1,189 rows).

> **2 columns never populated:** `lat`, `lon`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `target_scraper.py:188` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
