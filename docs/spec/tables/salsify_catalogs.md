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

| column | type | filled |
|---|---|---|
| `catalog_id` | `VARCHAR` | 100.0% |
| `org_id` | `VARCHAR` | 100.0% |
| `site_id` | `VARCHAR` | 100.0% |
| `catalog_name` | `VARCHAR` | 97.7% |
| `seeded` | `BOOLEAN` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `total_products` | `BIGINT` | 97.7% |
| `total_pages` | `BIGINT` | 97.7% |
| `page_size` | `BIGINT` | 97.7% |
| `has_sitemap` | `BOOLEAN` | **0.6%** |
| `allow_export` | `BOOLEAN` | 97.7% |
| `facet_properties` | `VARCHAR` | 86.2% |
| `publication_date` | `VARCHAR` | 33.3% |
| `build_id` | `VARCHAR` | 97.7% |
| `url` | `VARCHAR` | 100.0% |
| `checked_at` | `BIGINT` | 100.0% |

Fill measured over **full table** (520 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `salsify.py:350` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `salsify.py:767` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
