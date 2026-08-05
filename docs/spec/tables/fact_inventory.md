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
| `qty` | `DOUBLE` |
| `in_stock` | `BOOLEAN` |
| `stock_level` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `facts.py:152` | `write_partition` | partitioned (append-only parts) | no |
