# `src_skus`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,084,333 |
| Columns | 15 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/src_skus.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `source_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `upc_norm` | `VARCHAR` |
| `hoodie_sku` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `name_key` | `VARCHAR` |
| `product_type_id` | `BIGINT` |
| `product_type` | `VARCHAR` |
| `size_ml` | `BIGINT` |
| `container` | `VARCHAR` |
| `pack` | `BIGINT` |
| `pack_size` | `BIGINT` |
| `pack_type` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:233` | `write_parquet` | flat (full overwrite) | no |
