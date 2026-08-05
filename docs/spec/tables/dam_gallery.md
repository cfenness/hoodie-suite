# `dam_gallery`

|  |  |
|---|---|
| Status | landed |
| Rows | 66 |
| Columns | 25 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `dam-gallery` |
| URI | `s3://hoodie-suite-warehouse/warehouse/dam_gallery.parquet` |


## Columns

| column | type |
|---|---|
| `gallery_id` | `VARCHAR` |
| `source_id` | `VARCHAR` |
| `asset_id` | `BIGINT` |
| `asset_url` | `VARCHAR` |
| `vendor` | `VARCHAR` |
| `hoodie_brand_id` | `INTEGER` |
| `brand` | `INTEGER` |
| `brand_key` | `INTEGER` |
| `sku_id` | `INTEGER` |
| `sku_match_method` | `INTEGER` |
| `image_kind` | `VARCHAR` |
| `width` | `INTEGER` |
| `height` | `INTEGER` |
| `size_bytes` | `BIGINT` |
| `phash` | `INTEGER` |
| `phash_algo` | `INTEGER` |
| `embedding` | `INTEGER` |
| `embedding_backend` | `VARCHAR` |
| `embedding_dim` | `BIGINT` |
| `retention` | `VARCHAR` |
| `rights_ref` | `VARCHAR` |
| `image_use` | `VARCHAR` |
| `image_scope` | `VARCHAR` |
| `withheld_reason` | `VARCHAR` |
| `built_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dam_gallery.py:272` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
