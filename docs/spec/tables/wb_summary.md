# `wb_summary`

|  |  |
|---|---|
| Status | landed |
| Rows | 1 |
| Columns | 8 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/wb_summary.parquet` |


## Columns

| column | type |
|---|---|
| `total` | `BIGINT` |
| `multi` | `BIGINT` |
| `source_rows` | `BIGINT` |
| `by_source` | `VARCHAR` |
| `prod_total` | `BIGINT` |
| `prod_corr` | `BIGINT` |
| `ttb_corr` | `BIGINT` |
| `ttb_total` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `wb_views.py:328` | `write_parquet` | flat (full overwrite) | no |
