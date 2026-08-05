# `category_cluster`

|  |  |
|---|---|
| Status | landed |
| Rows | 782,928 |
| Columns | 7 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/category_cluster.parquet` |


## Columns

| column | type |
|---|---|
| `category_key` | `VARCHAR` |
| `canon` | `VARCHAR` |
| `members` | `BIGINT` |
| `n_sources` | `BIGINT` |
| `sources` | `VARCHAR` |
| `sample_name` | `VARCHAR` |
| `corroborated` | `BOOLEAN` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:586` | `write_parquet` | flat (full overwrite) | no |
