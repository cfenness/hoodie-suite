# `raw_payloads`

|  |  |
|---|---|
| Status | landed |
| Rows | 31,570,028 |
| Columns | 8 |
| Storage | partitioned |
| Partitions | 3,887 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/raw_payloads/2026-08-04_ubereats_s07_b0041.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `day` | `VARCHAR` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |
| `kind` | `VARCHAR` | 100.0% |
| `entity_id` | `VARCHAR` | 100.0% |
| `parent_id` | `VARCHAR` | 99.9% |
| `url` | `VARCHAR` | **0.1%** |
| `raw_json` | `VARCHAR` | 100.0% |

Fill measured over **newest 40 of 3887 partitions** (290,664 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `raw_capture.py:68` | `write_partition` | partitioned (append-only parts) | no |
