# `xwalk_item_identity`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,168,694 |
| Columns | 5 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/xwalk_item_identity.parquet` |


## Columns

| column | type |
|---|---|
| `item_key` | `VARCHAR` |
| `resolved_id` | `VARCHAR` |
| `n_sources` | `BIGINT` |
| `commercial_sources` | `BIGINT` |
| `tier` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:690` | `write_parquet` | flat (full overwrite) | no |
