# `ttb_cola`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,071,850 |
| Columns | 13 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `ttb-cola` |
| URI | `s3://hoodie-suite-warehouse/warehouse/ttb_cola.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `TTB ID` | `VARCHAR` | 100.0% |
| `Permit Number` | `VARCHAR` | 100.0% |
| `Serial Number` | `VARCHAR` | 100.0% |
| `Brand Name` | `VARCHAR` | 100.0% |
| `Fanciful Name` | `VARCHAR` | **0%** ‹never populated› |
| `Class/Type` | `VARCHAR` | 100.0% |
| `Origin` | `VARCHAR` | 100.0% |
| `Applicant` | `VARCHAR` | **0%** ‹never populated› |
| `Status` | `VARCHAR` | **0%** ‹never populated› |
| `Completed Date` | `VARCHAR` | 100.0% |
| `Approval Date` | `VARCHAR` | **0%** ‹never populated› |
| `Net Contents` | `VARCHAR` | **0%** ‹never populated› |
| `UPC` | `VARCHAR` | **0%** ‹never populated› |

Fill measured over **first 400,000 rows** (400,000 rows).

> **6 columns never populated:** `Fanciful Name`, `Applicant`, `Status`, `Approval Date`, `Net Contents`, `UPC`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `ttb_pull.py:45` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
