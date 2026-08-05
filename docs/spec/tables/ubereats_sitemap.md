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

| column | type | filled |
|---|---|---|
| `store_uuid` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `slug` | `VARCHAR` | 100.0% |
| `url` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).