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

| column | type | filled |
|---|---|---|
| `cust_id` | `VARCHAR` | 100.0% |
| `theme_version` | `VARCHAR` | 100.0% |
| `show_captcha` | `VARCHAR` | 100.0% |
| `brand_code` | `VARCHAR` | **0%** ‹never populated› |
| `brand_description` | `VARCHAR` | **0%** ‹never populated› |
| `menu_fields` | `VARCHAR` | 100.0% |
| `n_brands` | `BIGINT` | 100.0% |
| `default_zip` | `VARCHAR` | **0%** ‹never populated› |
| `default_address` | `VARCHAR` | **0%** ‹never populated› |
| `default_miles` | `VARCHAR` | 100.0% |
| `analytics` | `VARCHAR` | 100.0% |
| `map_style_code` | `VARCHAR` | 100.0% |
| `use_online_vendor` | `VARCHAR` | 100.0% |
| `n_bytes` | `BIGINT` | 100.0% |
| `first_seen` | `BIGINT` | 100.0% |
| `last_seen` | `BIGINT` | 100.0% |

Fill measured over **full table** (1,242 rows).

> **4 columns never populated:** `brand_code`, `brand_description`, `default_zip`, `default_address`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `vip_finder_census.py:463` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
