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

| column | type |
|---|---|
| `ts` | `VARCHAR` |
| `source_id` | `VARCHAR` |
| `rights_ref` | `VARCHAR` |
| `action` | `VARCHAR` |
| `subject` | `VARCHAR` |
| `surface` | `VARCHAR` |
| `allowed` | `BOOLEAN` |
| `reason` | `VARCHAR` |
| `image_use` | `VARCHAR` |
| `scope` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `rights.py:590` | `write_partition` | partitioned (append-only parts) | no |
