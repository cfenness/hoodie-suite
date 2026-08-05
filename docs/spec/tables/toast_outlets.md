# `toast_outlets`

|  |  |
|---|---|
| Status | landed |
| Rows | 85,284 |
| Columns | 6 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `toast` |
| URI | `s3://hoodie-suite-warehouse/warehouse/toast_outlets.parquet` |


## Columns

| column | type |
|---|---|
| `guid` | `VARCHAR` |
| `name` | `VARCHAR` |
| `slug` | `VARCHAR` |
| `url` | `VARCHAR` |
| `state` | `VARCHAR` |
| `source` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `toast.py:101` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
