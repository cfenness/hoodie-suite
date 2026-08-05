# `fact_inventory`

|  |  |
|---|---|
| Status | landed |
| Rows | 7,288,934 |
| Columns | 12 |
| Storage | partitioned |
| Partitions | 18 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/fact_inventory/2026-07-13_total-wine.parquet` |


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
| `qty` | `DOUBLE` | 82.2% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `stock_level` | `VARCHAR` | **0.2%** |

Fill measured over **newest 18 of 18 partitions** (7,288,934 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `facts.py:152` | `write_partition` | partitioned (append-only parts) | no |
