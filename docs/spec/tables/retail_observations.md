# `retail_observations`

|  |  |
|---|---|
| Status | landed |
| Rows | 59,077,605 |
| Columns | 19 |
| Storage | partitioned |
| Partitions | 4,296 |
| Schema drift | **3 schemas in a 6-partition sample** |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | yes |
| Written by sources | `abc-fws` |
| URI | `s3://hoodie-suite-warehouse/warehouse/retail_observations/2026-08-05_publix.parquet` |


## Columns

| column | type |
|---|---|
| `date` | `VARCHAR` |
| `observed_at` | `BIGINT` |
| `source` | `VARCHAR` |
| `chain` | `VARCHAR` |
| `store` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `gtin` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `name` | `VARCHAR` |
| `price` | `DOUBLE` |
| `promo` | `DOUBLE` |
| `promo_text` | `VARCHAR` |
| `on_promo` | `BOOLEAN` |
| `in_stock` | `BOOLEAN` |
| `qty` | `DOUBLE` |
| `stock_level` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `observe.py:156` | `write_partition` | partitioned (append-only parts) | yes |
