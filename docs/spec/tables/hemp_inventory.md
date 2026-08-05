# `hemp_inventory`

|  |  |
|---|---|
| Status | landed |
| Rows | 475 |
| Columns | 15 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `hemp-inventory` |
| URI | `s3://hoodie-suite-warehouse/warehouse/hemp_inventory.parquet` |


## Columns

| column | type |
|---|---|
| `retailer` | `VARCHAR` |
| `base` | `VARCHAR` |
| `state` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `variant_id` | `VARCHAR` |
| `sku` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `price` | `VARCHAR` |
| `available` | `BOOLEAN` |
| `qty` | `BIGINT` |
| `method` | `VARCHAR` |
| `signal` | `VARCHAR` |
| `captured_at` | `BIGINT` |
| `source` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `hemp_inventory.py:127` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
