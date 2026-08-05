# `src_items`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,059,584 |
| Columns | 11 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/src_items.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `source_id` | `VARCHAR` |
| `hoodie_item` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `name_key` | `VARCHAR` |
| `product_type_id` | `BIGINT` |
| `product_type` | `VARCHAR` |
| `size_ml` | `BIGINT` |
| `container` | `VARCHAR` |
| `volume_tier` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:230` | `write_parquet` | flat (full overwrite) | no |
