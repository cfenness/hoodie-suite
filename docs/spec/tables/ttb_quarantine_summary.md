# `ttb_quarantine_summary`

|  |  |
|---|---|
| Status | landed |
| Rows | 200 |
| Columns | 4 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/ttb_quarantine_summary.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `supplier` | `VARCHAR` | 100.0% |
| `candidates` | `BIGINT` | 100.0% |
| `brands` | `VARCHAR[]` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |

Fill measured over **full table** (200 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `master_ttb.py:135` | `write_parquet` | flat (full overwrite) | no |
