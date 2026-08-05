# `cityhive_chain_products`

|  |  |
|---|---|
| Status | **never landed** |
| Rows | — |
| Columns | — |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/cityhive_chain_products.parquet` |


> The table does not exist in the warehouse: `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/cityhive_chain_products.parquet' in region 'auto' (HTTP 404 Not Found)`


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `off_premise.py:731` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
