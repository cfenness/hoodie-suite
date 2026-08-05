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

| column | type | filled |
|---|---|---|
| `distributor_id` | `VARCHAR` | 100.0% |
| `distributor_name` | `VARCHAR` | 100.0% |
| `vip_source_id` | `BIGINT` | 100.0% |
| `vip_customer_id` | `BIGINT` | 100.0% |
| `category` | `VARCHAR` | 85.9% |
| `brand` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `product_name` | `VARCHAR` | 100.0% |
| `product_type` | `VARCHAR` | 100.0% |
| `style_name` | `VARCHAR` | 100.0% |
| `style_type` | `VARCHAR` | 99.7% |
| `sub_style_type` | `VARCHAR` | 87.1% |
| `abv` | `VARCHAR` | 80.5% |
| `ibu` | `VARCHAR` | 15.6% |
| `ratebeer_score` | `VARCHAR` | 8.6% |
| `ratebeer_style` | `VARCHAR` | 8.6% |
| `dist_item_code` | `VARCHAR` | 100.0% |
| `retail_upc` | `VARCHAR` | 97.8% |
| `retail_upc_raw` | `VARCHAR` | 97.8% |
| `package_name` | `VARCHAR` | 100.0% |
| `beverage_id` | `VARCHAR` | 100.0% |
| `image` | `VARCHAR` | 96.8% |
| `sell_sheet` | `VARCHAR` | 10.8% |
| `description` | `VARCHAR` | 71.3% |
| `pulled_at` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `vtinfo_bbs.py:234` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
