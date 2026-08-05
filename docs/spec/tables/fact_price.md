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

| column | type |
|---|---|
| `date` | `VARCHAR` |
| `source` | `VARCHAR` |
| `store_key` | `VARCHAR` |
| `hoodie_store_id` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `sku_key` | `INTEGER` |
| `hoodie_sku_id` | `INTEGER` |
| `price` | `DOUBLE` |
| `on_promo` | `BOOLEAN` |
| `promo` | `INTEGER` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `facts.py:154` | `write_partition` | partitioned (append-only parts) | no |
