# `abc_catalog`

|  |  |
|---|---|
| Status | landed |
| Rows | 14,098 |
| Columns | 7 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `abc-catalog`, `abc-fws` |
| URI | `s3://hoodie-suite-warehouse/warehouse/abc_catalog.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `sku` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | **0%** ‹never populated› |
| `size` | `VARCHAR` | 70.2% |
| `upc` | `VARCHAR` | 20.1% |
| `price` | `DOUBLE` | 100.0% |
| `url` | `VARCHAR` | 100.0% |

Fill measured over **full table** (14,098 rows).

> **1 column never populated:** `brand`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `abc_catalog.py:77` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `abc_catalog.py:68` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `abc_fws_scraper.py:435` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
