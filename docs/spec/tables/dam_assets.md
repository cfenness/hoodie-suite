# `dam_assets`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,490 |
| Columns | 28 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `dam-bacardi` |
| URI | `s3://hoodie-suite-warehouse/warehouse/dam_assets.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source_id` | `VARCHAR` | 100.0% |
| `vendor` | `VARCHAR` | 100.0% |
| `drive_id` | `BIGINT` | 100.0% |
| `drive_name` | `VARCHAR` | 100.0% |
| `folder_id` | `BIGINT` | 100.0% |
| `folder_path` | `VARCHAR` | 100.0% |
| `asset_id` | `BIGINT` | 100.0% |
| `asset_token` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `title` | `VARCHAR` | 100.0% |
| `description` | `VARCHAR` | 100.0% |
| `asset_type` | `VARCHAR` | 100.0% |
| `extension` | `VARCHAR` | 99.5% |
| `mime_type` | `VARCHAR` | **1.0%** |
| `size_bytes` | `BIGINT` | 100.0% |
| `asset_url` | `VARCHAR` | 100.0% |
| `thumb_url` | `VARCHAR` | 99.0% |
| `download_url` | `VARCHAR` | 100.0% |
| `created_on` | `VARCHAR` | 100.0% |
| `updated_on` | `VARCHAR` | 100.0% |
| `rights_ref` | `VARCHAR` | 100.0% |
| `image_use` | `VARCHAR` | 100.0% |
| `image_scope` | `VARCHAR` | 100.0% |
| `retention` | `VARCHAR` | 100.0% |
| `phash` | `INTEGER` | **0%** ‹never populated› |
| `embedding_ref` | `INTEGER` | **0%** ‹never populated› |
| `withheld_reason` | `VARCHAR` | 100.0% |
| `pulled_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (2,490 rows).

> **2 columns never populated:** `phash`, `embedding_ref`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dam.py:770` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
