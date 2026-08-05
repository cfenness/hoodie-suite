# `dam_assets`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,490 |
| Columns | 28 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `dam-bacardi` |
| URI | `s3://hoodie-suite-warehouse/warehouse/dam_assets.parquet` |


## Columns

| column | type |
|---|---|
| `source_id` | `VARCHAR` |
| `vendor` | `VARCHAR` |
| `drive_id` | `BIGINT` |
| `drive_name` | `VARCHAR` |
| `folder_id` | `BIGINT` |
| `folder_path` | `VARCHAR` |
| `asset_id` | `BIGINT` |
| `asset_token` | `VARCHAR` |
| `name` | `VARCHAR` |
| `title` | `VARCHAR` |
| `description` | `VARCHAR` |
| `asset_type` | `VARCHAR` |
| `extension` | `VARCHAR` |
| `mime_type` | `VARCHAR` |
| `size_bytes` | `BIGINT` |
| `asset_url` | `VARCHAR` |
| `thumb_url` | `VARCHAR` |
| `download_url` | `VARCHAR` |
| `created_on` | `VARCHAR` |
| `updated_on` | `VARCHAR` |
| `rights_ref` | `VARCHAR` |
| `image_use` | `VARCHAR` |
| `image_scope` | `VARCHAR` |
| `retention` | `VARCHAR` |
| `phash` | `INTEGER` |
| `embedding_ref` | `INTEGER` |
| `withheld_reason` | `VARCHAR` |
| `pulled_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dam.py:770` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
