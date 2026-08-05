# `winebow_brands`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,396 |
| Columns | 7 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `winebow` |
| URI | `s3://hoodie-suite-warehouse/warehouse/winebow_brands.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `brand` | `VARCHAR` | 100.0% |
| `website` | `VARCHAR` | 66.8% |
| `logo` | `VARCHAR` | 100.0% |
| `importer` | `VARCHAR` | 100.0% |
| `country` | `VARCHAR` | **0%** ‹never populated› |
| `product_type` | `VARCHAR` | **0%** ‹never populated› |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (1,396 rows).

> **2 columns never populated:** `country`, `product_type`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `winebow.py:86` | `write_parquet` | flat (full overwrite) | no |
