# `vip_brandbuilder_items`

|  |  |
|---|---|
| Status | landed |
| Rows | 698,807 |
| Columns | 25 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `vip-brandbuilder` |
| URI | `s3://hoodie-suite-warehouse/warehouse/vip_brandbuilder_items.parquet` |


## Columns

| column | type |
|---|---|
| `distributor_id` | `VARCHAR` |
| `distributor_name` | `VARCHAR` |
| `vip_source_id` | `BIGINT` |
| `vip_customer_id` | `BIGINT` |
| `category` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `product_type` | `VARCHAR` |
| `style_name` | `VARCHAR` |
| `style_type` | `VARCHAR` |
| `sub_style_type` | `VARCHAR` |
| `abv` | `VARCHAR` |
| `ibu` | `VARCHAR` |
| `ratebeer_score` | `VARCHAR` |
| `ratebeer_style` | `VARCHAR` |
| `dist_item_code` | `VARCHAR` |
| `retail_upc` | `VARCHAR` |
| `retail_upc_raw` | `VARCHAR` |
| `package_name` | `VARCHAR` |
| `beverage_id` | `VARCHAR` |
| `image` | `VARCHAR` |
| `sell_sheet` | `VARCHAR` |
| `description` | `VARCHAR` |
| `pulled_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `vtinfo_bbs.py:234` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
