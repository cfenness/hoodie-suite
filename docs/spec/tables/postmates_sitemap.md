# `postmates_sitemap`

|  |  |
|---|---|
| Status | landed |
| Rows | 269,007 |
| Columns | 6 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `postmates-sitemap` |
| URI | `s3://hoodie-suite-warehouse/warehouse/postmates_sitemap.parquet` |


## Columns

| column | type |
|---|---|
| `store_uuid` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `slug` | `VARCHAR` |
| `url` | `VARCHAR` |
| `source` | `VARCHAR` |
| `captured_at` | `BIGINT` |
