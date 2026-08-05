# `dist_item_xwalk`

|  |  |
|---|---|
| Status | landed |
| Rows | 755,221 |
| Columns | 10 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-dist-xwalk` |
| URI | `s3://hoodie-suite-warehouse/warehouse/dist_item_xwalk.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `distributor_id` | `VARCHAR` | 100.0% |
| `distributor_name` | `VARCHAR` | 100.0% |
| `dist_item_code` | `VARCHAR` | 100.0% |
| `dist_item_key` | `VARCHAR` | 100.0% |
| `retail_upc` | `VARCHAR` | 97.2% |
| `canon_item_id` | `BIGINT` | 97.2% |
| `brand` | `VARCHAR` | 100.0% |
| `product_name` | `VARCHAR` | 100.0% |
| `size_raw` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dist_xwalk.py:119` | `write_parquet` | flat (full overwrite) | no |
