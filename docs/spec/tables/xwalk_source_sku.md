# `xwalk_source_sku`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,338,594 |
| Columns | 5 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/xwalk_source_sku.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `product_key` | `VARCHAR` | 100.0% |
| `item_key` | `VARCHAR` | 100.0% |
| `sku_key` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:498` | `write_parquet` | flat (full overwrite) | no |
