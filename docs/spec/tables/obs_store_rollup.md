# `obs_store_rollup`

|  |  |
|---|---|
| Status | landed |
| Rows | 215,203 |
| Columns | 20 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full rebuild, layout-preserving) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/obs_store_rollup.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `chain` | `VARCHAR` | **0%** ‹never populated› |
| `items` | `BIGINT` | 100.0% |
| `brands` | `BIGINT` | 100.0% |
| `obs_rows` | `BIGINT` | 100.0% |
| `stores_seen_days` | `BIGINT` | 100.0% |
| `first_date` | `VARCHAR` | 100.0% |
| `last_date` | `VARCHAR` | 100.0% |
| `price_min` | `DOUBLE` | 100.0% |
| `price_p25` | `DOUBLE` | 100.0% |
| `price_median` | `DOUBLE` | 100.0% |
| `price_p75` | `DOUBLE` | 100.0% |
| `price_max` | `DOUBLE` | 100.0% |
| `price_avg` | `DOUBLE` | 100.0% |
| `promo_rows` | `BIGINT` | 100.0% |
| `promo_share` | `DOUBLE` | 100.0% |
| `in_stock_share` | `DOUBLE` | 100.0% |
| `hemp_items` | `BIGINT` | 100.0% |
| `built_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (215,203 rows).

> **1 column never populated:** `chain`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `obs_rollup.py:239` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
