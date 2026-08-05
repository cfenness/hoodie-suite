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

| column | type | filled |
|---|---|---|
| `dimension` | `VARCHAR` | 100.0% |
| `variant_key` | `VARCHAR` | 100.0% |
| `variant` | `VARCHAR` | 100.0% |
| `canonical` | `VARCHAR` | 100.0% |
| `times` | `BIGINT` | 100.0% |
| `updated_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (6 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `xsource_queue.py:313` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
