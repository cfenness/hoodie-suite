# `hemp_retailers`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,144 |
| Columns | 13 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `hemp-finder` |
| URI | `s3://hoodie-suite-warehouse/warehouse/hemp_retailers.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `brand` | `VARCHAR` | 100.0% |
| `account` | `VARCHAR` | 100.0% |
| `chain` | `VARCHAR` | 100.0% |
| `street` | `VARCHAR` | 100.0% |
| `city` | `VARCHAR` | 100.0% |
| `state` | `VARCHAR` | 100.0% |
| `zip` | `VARCHAR` | **0%** ‹never populated› |
| `phone` | `VARCHAR` | 100.0% |
| `lat` | `VARCHAR` | 100.0% |
| `lng` | `VARCHAR` | 100.0% |
| `store_type` | `VARCHAR` | **0%** ‹never populated› |
| `source` | `VARCHAR` | 100.0% |
| `zip_searched` | `VARCHAR` | 100.0% |

Fill measured over **full table** (2,144 rows).

> **2 columns never populated:** `zip`, `store_type`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `hemp_finder.py:85` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
