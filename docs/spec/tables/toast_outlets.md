# `toast_outlets`

|  |  |
|---|---|
| Status | landed |
| Rows | 85,284 |
| Columns | 6 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `toast` |
| URI | `s3://hoodie-suite-warehouse/warehouse/toast_outlets.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `guid` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `slug` | `VARCHAR` | 100.0% |
| `url` | `VARCHAR` | 100.0% |
| `state` | `VARCHAR` | **0%** ‹never populated› |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (85,284 rows).

> **1 column never populated:** `state`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `toast.py:101` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
