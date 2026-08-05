# `raw_payloads`

|  |  |
|---|---|
| Status | landed |
| Rows | 31,573,796 |
| Columns | 8 |
| Storage | partitioned |
| Partitions | 3,889 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/raw_payloads/2026-08-05_postmates_s06_b0001.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `day` | `VARCHAR` |
| `captured_at` | `BIGINT` |
| `kind` | `VARCHAR` |
| `entity_id` | `VARCHAR` |
| `parent_id` | `VARCHAR` |
| `url` | `VARCHAR` |
| `raw_json` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `raw_capture.py:68` | `write_partition` | partitioned (append-only parts) | no |
