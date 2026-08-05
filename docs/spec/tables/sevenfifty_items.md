# `sevenfifty_items`

|  |  |
|---|---|
| Status | landed |
| Rows | 25,785 |
| Columns | 28 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `sevenfifty` |
| URI | `s3://hoodie-suite-warehouse/warehouse/sevenfifty_items.parquet` |


## Columns

| column | type |
|---|---|
| `storefront` | `VARCHAR` |
| `distributor` | `VARCHAR` |
| `sku` | `VARCHAR` |
| `sevenfifty_id` | `BIGINT` |
| `token` | `VARCHAR` |
| `name` | `VARCHAR` |
| `name_full` | `VARCHAR` |
| `producer` | `VARCHAR` |
| `supplier` | `VARCHAR` |
| `product_type` | `VARCHAR` |
| `style` | `VARCHAR` |
| `style_line` | `VARCHAR` |
| `subtype` | `VARCHAR` |
| `appellation` | `VARCHAR` |
| `country` | `VARCHAR` |
| `region` | `VARCHAR` |
| `subregion` | `VARCHAR` |
| `size` | `DOUBLE` |
| `size_formatted` | `VARCHAR` |
| `case_size` | `BIGINT` |
| `container_type` | `VARCHAR` |
| `raw_materials` | `VARCHAR` |
| `status` | `VARCHAR` |
| `vendor_id` | `BIGINT` |
| `image_url` | `VARCHAR` |
| `thumbnail_url` | `VARCHAR` |
| `display_url` | `VARCHAR` |
| `pulled_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `sevenfifty.py:179` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
