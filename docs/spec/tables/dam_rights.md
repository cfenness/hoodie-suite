# `dam_rights`

|  |  |
|---|---|
| Status | landed |
| Rows | 1 |
| Columns | 27 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/dam_rights.parquet` |


## Columns

| column | type |
|---|---|
| `source_id` | `VARCHAR` |
| `vendor` | `VARCHAR` |
| `host` | `VARCHAR` |
| `tos_url` | `VARCHAR` |
| `tos_sha256` | `VARCHAR` |
| `tos_captured_at` | `VARCHAR` |
| `tos_capture_method` | `VARCHAR` |
| `robots_sha256` | `VARCHAR` |
| `robots_checked_at` | `VARCHAR` |
| `robots_allows_harvest` | `BOOLEAN` |
| `facts_use` | `VARCHAR` |
| `image_use` | `VARCHAR` |
| `scope` | `VARCHAR` |
| `attribution_required` | `BOOLEAN` |
| `alteration_allowed` | `INTEGER` |
| `expiry` | `INTEGER` |
| `confidence` | `VARCHAR` |
| `needs_counsel` | `BOOLEAN` |
| `counsel_cleared` | `BOOLEAN` |
| `review_state` | `VARCHAR` |
| `schema_version` | `VARCHAR` |
| `schema_signoff` | `INTEGER` |
| `reviewed_by` | `VARCHAR` |
| `reviewed_at` | `VARCHAR` |
| `escalation` | `VARCHAR` |
| `evidence_json` | `VARCHAR` |
| `landed_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `rights.py:620` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
