# `field_stats`

|  |  |
|---|---|
| Status | landed |
| Rows | 179 |
| Columns | 7 |
| Storage | partitioned |
| Partitions | 16 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/field_stats/2026-08-05_postmates_postmates_valuerules.parquet` |


## Columns

| column | type |
|---|---|
| `day` | `VARCHAR` |
| `source` | `VARCHAR` |
| `table_name` | `VARCHAR` |
| `field` | `VARCHAR` |
| `rows` | `BIGINT` |
| `filled` | `BIGINT` |
| `fill_pct` | `DOUBLE` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `extract_qa.py:128` | `write_partition` | partitioned (append-only parts) | no |
