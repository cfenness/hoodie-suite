# `salsify_catalogs`

|  |  |
|---|---|
| Status | landed |
| Rows | 520 |
| Columns | 16 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `salsify` |
| URI | `s3://hoodie-suite-warehouse/warehouse/salsify_catalogs.parquet` |


## Columns

| column | type |
|---|---|
| `catalog_id` | `VARCHAR` |
| `org_id` | `VARCHAR` |
| `site_id` | `VARCHAR` |
| `catalog_name` | `VARCHAR` |
| `seeded` | `BOOLEAN` |
| `status` | `VARCHAR` |
| `total_products` | `BIGINT` |
| `total_pages` | `BIGINT` |
| `page_size` | `BIGINT` |
| `has_sitemap` | `BOOLEAN` |
| `allow_export` | `BOOLEAN` |
| `facet_properties` | `VARCHAR` |
| `publication_date` | `VARCHAR` |
| `build_id` | `VARCHAR` |
| `url` | `VARCHAR` |
| `checked_at` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `salsify.py:350` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `salsify.py:767` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
