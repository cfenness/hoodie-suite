# `salsify_properties`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,870,998 |
| Columns | 10 |
| Storage | partitioned |
| Partitions | 314 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `bbg`, `salsify` |
| URI | `s3://hoodie-suite-warehouse/warehouse/salsify_properties/2026-08-05_sazerac_035413_p0001.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `catalog_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `group` | `VARCHAR` | 100.0% |
| `property` | `VARCHAR` | 100.0% |
| `label` | `VARCHAR` | 100.0% |
| `value_index` | `BIGINT` | 100.0% |
| `value` | `VARCHAR` | 100.0% |
| `asset_name` | `VARCHAR` | 6.4% |
| `day` | `VARCHAR` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |

Fill measured over **newest 40 of 314 partitions** (925,273 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `salsify.py:873` | `write_partition` | partitioned (append-only parts) | no |
