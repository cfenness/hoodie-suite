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

| column | type |
|---|---|
| `catalog_id` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `group` | `VARCHAR` |
| `property` | `VARCHAR` |
| `label` | `VARCHAR` |
| `value_index` | `BIGINT` |
| `value` | `VARCHAR` |
| `asset_name` | `VARCHAR` |
| `day` | `VARCHAR` |
| `captured_at` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `salsify.py:873` | `write_partition` | partitioned (append-only parts) | no |
