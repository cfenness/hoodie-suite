# `ubereats_sitemap`

|  |  |
|---|---|
| Status | landed |
| Rows | 755,032 |
| Columns | 6 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `ubereats-sitemap` |
| URI | `s3://hoodie-suite-warehouse/warehouse/ubereats_sitemap.parquet` |


## Columns

| column | type |
|---|---|
| `store_uuid` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `slug` | `VARCHAR` |
| `url` | `VARCHAR` |
| `source` | `VARCHAR` |
| `captured_at` | `BIGINT` |
