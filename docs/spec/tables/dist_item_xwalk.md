# `dist_item_xwalk`

|  |  |
|---|---|
| Status | landed |
| Rows | 755,221 |
| Columns | 10 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-dist-xwalk` |
| URI | `s3://hoodie-suite-warehouse/warehouse/dist_item_xwalk.parquet` |


## Columns

| column | type |
|---|---|
| `distributor_id` | `VARCHAR` |
| `distributor_name` | `VARCHAR` |
| `dist_item_code` | `VARCHAR` |
| `dist_item_key` | `VARCHAR` |
| `retail_upc` | `VARCHAR` |
| `canon_item_id` | `BIGINT` |
| `brand` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `size_raw` | `VARCHAR` |
| `source` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dist_xwalk.py:119` | `write_parquet` | flat (full overwrite) | no |
