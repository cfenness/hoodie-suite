# `ttb_master`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,732 |
| Columns | 18 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `ttb` |
| URI | `s3://hoodie-suite-warehouse/warehouse/ttb_master.parquet` |


## Columns

| column | type |
|---|---|
| `cluster_id` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `fanciful` | `VARCHAR` |
| `class_type` | `VARCHAR` |
| `size_ml` | `VARCHAR` |
| `supplier` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `corroborated_by` | `VARCHAR` |
| `confidence` | `DOUBLE` |
| `match_kind` | `VARCHAR` |
| `size_matched` | `BOOLEAN` |
| `candidate_name` | `VARCHAR` |
| `matched_by` | `VARCHAR` |
| `member_count` | `BIGINT` |
| `members` | `VARCHAR` |
| `first_day` | `BIGINT` |
| `last_day` | `BIGINT` |
| `tier` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `master_ttb.py:133` | `write_parquet` | flat (full overwrite) | no |
| `server.py:3477` | `write_parquet` | flat (full overwrite) | no |
