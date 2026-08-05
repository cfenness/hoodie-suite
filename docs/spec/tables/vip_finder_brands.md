# `vip_finder_brands`

|  |  |
|---|---|
| Status | landed |
| Rows | 33,196 |
| Columns | 4 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `vip-finder-census` |
| URI | `s3://hoodie-suite-warehouse/warehouse/vip_finder_brands.parquet` |


## Columns

| column | type |
|---|---|
| `cust_id` | `VARCHAR` |
| `brand_value` | `VARCHAR` |
| `brand_label` | `VARCHAR` |
| `last_seen` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `vip_finder_census.py:466` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
