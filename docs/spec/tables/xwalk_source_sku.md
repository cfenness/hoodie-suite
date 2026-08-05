# `xwalk_source_sku`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,338,594 |
| Columns | 5 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/xwalk_source_sku.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `product_key` | `VARCHAR` |
| `item_key` | `VARCHAR` |
| `sku_key` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:498` | `write_parquet` | flat (full overwrite) | no |
