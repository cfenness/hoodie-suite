# `field_stats`

|  |  |
|---|---|
| Status | landed |
| Rows | 178 |
| Columns | 7 |
| Storage | partitioned |
| Partitions | 16 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/field_stats/2026-08-03_ubereats_ubereats_valuerules.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `day` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `table_name` | `VARCHAR` | 100.0% |
| `field` | `VARCHAR` | 100.0% |
| `rows` | `BIGINT` | 100.0% |
| `filled` | `BIGINT` | 100.0% |
| `fill_pct` | `DOUBLE` | 100.0% |

Fill measured over **newest 16 of 16 partitions** (178 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `extract_qa.py:128` | `write_partition` | partitioned (append-only parts) | no |
