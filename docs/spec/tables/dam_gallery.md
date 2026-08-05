# `dam_gallery`

|  |  |
|---|---|
| Status | landed |
| Rows | 66 |
| Columns | 25 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `dam-gallery` |
| URI | `s3://hoodie-suite-warehouse/warehouse/dam_gallery.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `gallery_id` | `VARCHAR` | 100.0% |
| `source_id` | `VARCHAR` | 100.0% |
| `asset_id` | `BIGINT` | 100.0% |
| `asset_url` | `VARCHAR` | 100.0% |
| `vendor` | `VARCHAR` | 100.0% |
| `hoodie_brand_id` | `INTEGER` | **0%** ‹never populated› |
| `brand` | `INTEGER` | **0%** ‹never populated› |
| `brand_key` | `INTEGER` | **0%** ‹never populated› |
| `sku_id` | `INTEGER` | **0%** ‹never populated› |
| `sku_match_method` | `INTEGER` | **0%** ‹never populated› |
| `image_kind` | `VARCHAR` | 100.0% |
| `width` | `INTEGER` | **0%** ‹never populated› |
| `height` | `INTEGER` | **0%** ‹never populated› |
| `size_bytes` | `BIGINT` | 100.0% |
| `phash` | `INTEGER` | **0%** ‹never populated› |
| `phash_algo` | `INTEGER` | **0%** ‹never populated› |
| `embedding` | `INTEGER` | **0%** ‹never populated› |
| `embedding_backend` | `VARCHAR` | 100.0% |
| `embedding_dim` | `BIGINT` | 100.0% |
| `retention` | `VARCHAR` | 100.0% |
| `rights_ref` | `VARCHAR` | 100.0% |
| `image_use` | `VARCHAR` | 100.0% |
| `image_scope` | `VARCHAR` | 100.0% |
| `withheld_reason` | `VARCHAR` | 100.0% |
| `built_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (66 rows).

> **10 columns never populated:** `hoodie_brand_id`, `brand`, `brand_key`, `sku_id`, `sku_match_method`, `width`, `height`, `phash`, `phash_algo`, `embedding`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dam_gallery.py:272` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
