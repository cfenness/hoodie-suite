# `hemp_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 4,040 |
| Columns | 11 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `hemp-scan` |
| URI | `s3://hoodie-suite-warehouse/warehouse/hemp_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 79.7% |
| `category` | `VARCHAR` | 84.6% |
| `upc` | `VARCHAR` | 14.8% |
| `size_ml` | `VARCHAR` | **3.3%** |
| `price` | `VARCHAR` | 33.8% |
| `image` | `VARCHAR` | 71.4% |
| `state` | `VARCHAR` | **0%** ‹never populated› |
| `url` | `VARCHAR` | 15.1% |
| `signal` | `VARCHAR` | 100.0% |

Fill measured over **full table** (4,040 rows).

> **1 column never populated:** `state`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `hemp_scan.py:120` | `write_parquet` | flat (full overwrite) | no |
