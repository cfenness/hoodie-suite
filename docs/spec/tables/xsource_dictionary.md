# `xsource_dictionary`

|  |  |
|---|---|
| Status | landed |
| Rows | 6 |
| Columns | 6 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/xsource_dictionary.parquet` |


## Columns

| column | type |
|---|---|
| `dimension` | `VARCHAR` |
| `variant_key` | `VARCHAR` |
| `variant` | `VARCHAR` |
| `canonical` | `VARCHAR` |
| `times` | `BIGINT` |
| `updated_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `xsource_queue.py:313` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
