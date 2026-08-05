# `stop_and_shop_products`

|  |  |
|---|---|
| Status | **never landed** |
| Rows | — |
| Columns | — |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `stop-and-shop` |
| URI | `s3://hoodie-suite-warehouse/warehouse/stop_and_shop_products.parquet` |


> The table does not exist in the warehouse: `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/stop_and_shop_products.parquet' in region 'auto' (HTTP 404 Not Found)`


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `stop_and_shop.py:125` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
