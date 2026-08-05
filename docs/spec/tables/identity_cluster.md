# `identity_cluster`

|  |  |
|---|---|
| Status | landed |
| Rows | 776,316 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/identity_cluster.parquet` |


## Columns

| column | type |
|---|---|
| `cluster_id` | `VARCHAR` |
| `members` | `BIGINT` |
| `n_sources` | `BIGINT` |
| `commercial_sources` | `BIGINT` |
| `has_ttb` | `BOOLEAN` |
| `tier` | `BIGINT` |
| `sources` | `VARCHAR` |
| `sample_name` | `VARCHAR` |
| `corroborated` | `BOOLEAN` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:666` | `write_parquet` | flat (full overwrite) | no |
