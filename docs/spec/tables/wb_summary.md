# `wb_summary`

|  |  |
|---|---|
| Status | landed |
| Rows | 1 |
| Columns | 8 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/wb_summary.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `total` | `BIGINT` | 100.0% |
| `multi` | `BIGINT` | 100.0% |
| `source_rows` | `BIGINT` | 100.0% |
| `by_source` | `VARCHAR` | 100.0% |
| `prod_total` | `BIGINT` | 100.0% |
| `prod_corr` | `BIGINT` | 100.0% |
| `ttb_corr` | `BIGINT` | 100.0% |
| `ttb_total` | `BIGINT` | 100.0% |

Fill measured over **full table** (1 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `wb_views.py:328` | `write_parquet` | flat (full overwrite) | no |
