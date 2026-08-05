# `vip_brandbuilder_directory`

|  |  |
|---|---|
| Status | landed |
| Rows | 365 |
| Columns | 8 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `vip-brandbuilder-census` |
| URI | `s3://hoodie-suite-warehouse/warehouse/vip_brandbuilder_directory.parquet` |


## Columns

| column | type |
|---|---|
| `source_code` | `VARCHAR` |
| `status` | `VARCHAR` |
| `distributor_name` | `VARCHAR` |
| `vip_source_id` | `BIGINT` |
| `vip_customer_id` | `BIGINT` |
| `n_products` | `BIGINT` |
| `first_seen` | `BIGINT` |
| `last_seen` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `vip_brandbuilder_census.py:347` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
