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

| column | type | filled |
|---|---|---|
| `item_key` | `VARCHAR` | 100.0% |
| `resolved_id` | `VARCHAR` | 100.0% |
| `n_sources` | `BIGINT` | 100.0% |
| `commercial_sources` | `BIGINT` | 100.0% |
| `tier` | `BIGINT` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:707` | `write_parquet` | flat (full overwrite) | no |
