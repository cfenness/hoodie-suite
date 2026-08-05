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

| column | type | filled |
|---|---|---|
| `source_code` | `VARCHAR` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `distributor_name` | `VARCHAR` | 100.0% |
| `vip_source_id` | `BIGINT` | 100.0% |
| `vip_customer_id` | `BIGINT` | 100.0% |
| `n_products` | `BIGINT` | 100.0% |
| `first_seen` | `BIGINT` | 100.0% |
| `last_seen` | `BIGINT` | 100.0% |

Fill measured over **full table** (365 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `vip_brandbuilder_census.py:347` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
