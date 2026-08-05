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

| column | type |
|---|---|
| `TTB ID` | `VARCHAR` |
| `Permit Number` | `VARCHAR` |
| `Serial Number` | `VARCHAR` |
| `Brand Name` | `VARCHAR` |
| `Fanciful Name` | `VARCHAR` |
| `Class/Type` | `VARCHAR` |
| `Origin` | `VARCHAR` |
| `Applicant` | `VARCHAR` |
| `Status` | `VARCHAR` |
| `Completed Date` | `VARCHAR` |
| `Approval Date` | `VARCHAR` |
| `Net Contents` | `VARCHAR` |
| `UPC` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `ttb_pull.py:45` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
