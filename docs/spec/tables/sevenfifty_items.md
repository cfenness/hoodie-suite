# `sevenfifty_items`

|  |  |
|---|---|
| Status | landed |
| Rows | 25,785 |
| Columns | 28 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `sevenfifty` |
| URI | `s3://hoodie-suite-warehouse/warehouse/sevenfifty_items.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `storefront` | `VARCHAR` | 100.0% |
| `distributor` | `VARCHAR` | 100.0% |
| `sku` | `VARCHAR` | 100.0% |
| `sevenfifty_id` | `BIGINT` | 100.0% |
| `token` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `name_full` | `VARCHAR` | 100.0% |
| `producer` | `VARCHAR` | 100.0% |
| `supplier` | `VARCHAR` | 100.0% |
| `product_type` | `VARCHAR` | 100.0% |
| `style` | `VARCHAR` | 100.0% |
| `style_line` | `VARCHAR` | 100.0% |
| `subtype` | `VARCHAR` | 100.0% |
| `appellation` | `VARCHAR` | 28.6% |
| `country` | `VARCHAR` | 100.0% |
| `region` | `VARCHAR` | 78.6% |
| `subregion` | `VARCHAR` | 15.1% |
| `size` | `DOUBLE` | 100.0% |
| `size_formatted` | `VARCHAR` | 100.0% |
| `case_size` | `BIGINT` | 100.0% |
| `container_type` | `VARCHAR` | 100.0% |
| `raw_materials` | `VARCHAR` | 43.7% |
| `status` | `VARCHAR` | 100.0% |
| `vendor_id` | `BIGINT` | 100.0% |
| `image_url` | `VARCHAR` | 99.6% |
| `thumbnail_url` | `VARCHAR` | 99.6% |
| `display_url` | `VARCHAR` | 100.0% |
| `pulled_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (25,785 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `sevenfifty.py:179` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
