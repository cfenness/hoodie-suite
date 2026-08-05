# `hemp_retailers`

|  |  |
|---|---|
| Status | landed |
| Rows | 2,144 |
| Columns | 13 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `hemp-finder` |
| URI | `s3://hoodie-suite-warehouse/warehouse/hemp_retailers.parquet` |


## Columns

| column | type |
|---|---|
| `brand` | `VARCHAR` |
| `account` | `VARCHAR` |
| `chain` | `VARCHAR` |
| `street` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `zip` | `VARCHAR` |
| `phone` | `VARCHAR` |
| `lat` | `VARCHAR` |
| `lng` | `VARCHAR` |
| `store_type` | `VARCHAR` |
| `source` | `VARCHAR` |
| `zip_searched` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `hemp_finder.py:85` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
