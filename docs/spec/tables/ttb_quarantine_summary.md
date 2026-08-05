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

| column | type |
|---|---|
| `supplier` | `VARCHAR` |
| `candidates` | `BIGINT` |
| `brands` | `VARCHAR[]` |
| `run_id` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `master_ttb.py:135` | `write_parquet` | flat (full overwrite) | no |
