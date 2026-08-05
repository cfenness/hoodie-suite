# `vtinfo_titos`

|  |  |
|---|---|
| Status | landed |
| Rows | 399 |
| Columns | 13 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `vtinfo` |
| URI | `s3://hoodie-suite-warehouse/warehouse/vtinfo_titos.parquet` |


## Columns

| column | type |
|---|---|
| `Brand` | `VARCHAR` |
| `Account` | `VARCHAR` |
| `Street` | `VARCHAR` |
| `City` | `VARCHAR` |
| `State` | `VARCHAR` |
| `Zip` | `VARCHAR` |
| `Phone` | `VARCHAR` |
| `Miles` | `VARCHAR` |
| `Lat` | `VARCHAR` |
| `Lng` | `VARCHAR` |
| `StoreType` | `VARCHAR` |
| `Source` | `VARCHAR` |
| `Zip_Searched` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `vtinfo.py:277` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
