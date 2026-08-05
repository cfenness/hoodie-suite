# `dam_census`

|  |  |
|---|---|
| Status | landed |
| Rows | 67 |
| Columns | 26 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `dam-census` |
| URI | `s3://hoodie-suite-warehouse/warehouse/dam_census.parquet` |


## Columns

| column | type |
|---|---|
| `supplier` | `VARCHAR` |
| `corporate_domain` | `VARCHAR` |
| `media_url` | `VARCHAR` |
| `media_host` | `VARCHAR` |
| `dam_vendor` | `VARCHAR` |
| `vendor_signals` | `VARCHAR` |
| `vendor_confidence` | `VARCHAR` |
| `kind` | `VARCHAR` |
| `public` | `BOOLEAN` |
| `drive_id` | `INTEGER` |
| `company_id` | `INTEGER` |
| `reachable` | `BOOLEAN` |
| `http_status` | `BIGINT` |
| `robots_allows` | `BOOLEAN` |
| `tos_url` | `VARCHAR` |
| `tos_chars` | `BIGINT` |
| `tos_capture` | `VARCHAR` |
| `image_use` | `VARCHAR` |
| `scope` | `VARCHAR` |
| `confidence` | `VARCHAR` |
| `needs_counsel` | `BOOLEAN` |
| `provisional` | `BOOLEAN` |
| `discovery_method` | `VARCHAR` |
| `connector` | `VARCHAR` |
| `notes` | `VARCHAR` |
| `checked_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dam_census.py:654` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
