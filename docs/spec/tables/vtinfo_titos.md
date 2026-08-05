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

| column | type | filled |
|---|---|---|
| `Brand` | `VARCHAR` | 100.0% |
| `Account` | `VARCHAR` | 100.0% |
| `Street` | `VARCHAR` | 100.0% |
| `City` | `VARCHAR` | 99.7% |
| `State` | `VARCHAR` | 99.7% |
| `Zip` | `VARCHAR` | **0%** ‹never populated› |
| `Phone` | `VARCHAR` | 100.0% |
| `Miles` | `VARCHAR` | 100.0% |
| `Lat` | `VARCHAR` | 100.0% |
| `Lng` | `VARCHAR` | 100.0% |
| `StoreType` | `VARCHAR` | **0%** ‹never populated› |
| `Source` | `VARCHAR` | 100.0% |
| `Zip_Searched` | `VARCHAR` | 100.0% |

Fill measured over **full table** (399 rows).

> **2 columns never populated:** `Zip`, `StoreType`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `vtinfo.py:277` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
