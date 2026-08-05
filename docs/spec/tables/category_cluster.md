# `category_cluster`

|  |  |
|---|---|
| Status | landed |
| Rows | 782,928 |
| Columns | 7 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/category_cluster.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `category_key` | `VARCHAR` | 100.0% |
| `canon` | `VARCHAR` | 100.0% |
| `members` | `BIGINT` | 100.0% |
| `n_sources` | `BIGINT` | 100.0% |
| `sources` | `VARCHAR` | 100.0% |
| `sample_name` | `VARCHAR` | 100.0% |
| `corroborated` | `BOOLEAN` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:586` | `write_parquet` | flat (full overwrite) | no |
