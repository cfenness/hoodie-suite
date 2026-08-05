# `fact_price`

|  |  |
|---|---|
| Status | landed |
| Rows | 7,149,063 |
| Columns | 12 |
| Storage | partitioned |
| Partitions | 18 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/fact_price/2026-07-13_total-wine.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `date` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `store_key` | `VARCHAR` | 100.0% |
| `hoodie_store_id` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | **0.7%** |
| `sku_key` | `INTEGER` | 97.7% |
| `hoodie_sku_id` | `INTEGER` | 97.7% |
| `price` | `DOUBLE` | 100.0% |
| `on_promo` | `BOOLEAN` | 100.0% |
| `promo` | `INTEGER` | **0.5%** |

Fill measured over **newest 18 of 18 partitions** (7,149,063 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `facts.py:154` | `write_partition` | partitioned (append-only parts) | no |
