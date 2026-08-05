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

| column | type | filled |
|---|---|---|
| `cluster_id` | `VARCHAR` | 100.0% |
| `members` | `BIGINT` | 100.0% |
| `n_sources` | `BIGINT` | 100.0% |
| `commercial_sources` | `BIGINT` | 100.0% |
| `has_ttb` | `BOOLEAN` | 100.0% |
| `tier` | `BIGINT` | 100.0% |
| `sources` | `VARCHAR` | 100.0% |
| `sample_name` | `VARCHAR` | 100.0% |
| `corroborated` | `BOOLEAN` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:683` | `write_parquet` | flat (full overwrite) | no |
