# `dam_emissions`

|  |  |
|---|---|
| Status | landed |
| Rows | 1 |
| Columns | 10 |
| Storage | partitioned |
| Partitions | 1 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | yes |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/dam_emissions/2026-08-04_dam-bacardi.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `ts` | `VARCHAR` | 100.0% |
| `source_id` | `VARCHAR` | 100.0% |
| `rights_ref` | `VARCHAR` | 100.0% |
| `action` | `VARCHAR` | 100.0% |
| `subject` | `VARCHAR` | 100.0% |
| `surface` | `VARCHAR` | 100.0% |
| `allowed` | `BOOLEAN` | 100.0% |
| `reason` | `VARCHAR` | 100.0% |
| `image_use` | `VARCHAR` | 100.0% |
| `scope` | `VARCHAR` | 100.0% |

Fill measured over **newest 1 of 1 partitions** (1 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `rights.py:590` | `write_partition` | partitioned (append-only parts) | no |
