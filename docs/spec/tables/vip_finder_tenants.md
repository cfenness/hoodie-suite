# `vip_finder_tenants`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,242 |
| Columns | 16 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `vip-finder-census` |
| URI | `s3://hoodie-suite-warehouse/warehouse/vip_finder_tenants.parquet` |


## Columns

| column | type |
|---|---|
| `cust_id` | `VARCHAR` |
| `theme_version` | `VARCHAR` |
| `show_captcha` | `VARCHAR` |
| `brand_code` | `VARCHAR` |
| `brand_description` | `VARCHAR` |
| `menu_fields` | `VARCHAR` |
| `n_brands` | `BIGINT` |
| `default_zip` | `VARCHAR` |
| `default_address` | `VARCHAR` |
| `default_miles` | `VARCHAR` |
| `analytics` | `VARCHAR` |
| `map_style_code` | `VARCHAR` |
| `use_online_vendor` | `VARCHAR` |
| `n_bytes` | `BIGINT` |
| `first_seen` | `BIGINT` |
| `last_seen` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `vip_finder_census.py:463` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
