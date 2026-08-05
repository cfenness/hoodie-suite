# `item_identity`

|  |  |
|---|---|
| Status | landed |
| Rows | 147,235 |
| Columns | 14 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-item-identity` |
| URI | `s3://hoodie-suite-warehouse/warehouse/item_identity.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `canon_item_id` | `BIGINT` |
| `brand` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `category` | `VARCHAR` |
| `size_ml` | `DOUBLE` |
| `size_raw` | `VARCHAR` |
| `upcs` | `VARCHAR[]` |
| `identity_key` | `VARCHAR` |
| `status` | `VARCHAR` |
| `merged_into` | `INTEGER` |
| `match_tier` | `BIGINT` |
| `method_version` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_item_identity.py:105` | `write_parquet` | flat (full overwrite) | no |
| `ingest_canon_identity.py:55` | `write_parquet` | flat (full overwrite) | no |
