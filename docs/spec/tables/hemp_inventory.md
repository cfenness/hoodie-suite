# `hemp_inventory`

|  |  |
|---|---|
| Status | landed |
| Rows | 475 |
| Columns | 15 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `hemp-inventory` |
| URI | `s3://hoodie-suite-warehouse/warehouse/hemp_inventory.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `retailer` | `VARCHAR` | 100.0% |
| `base` | `VARCHAR` | 100.0% |
| `state` | `VARCHAR` | **0%** ‹never populated› |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `variant_id` | `VARCHAR` | 100.0% |
| `sku` | `VARCHAR` | 77.7% |
| `upc` | `VARCHAR` | **0%** ‹never populated› |
| `price` | `VARCHAR` | 100.0% |
| `available` | `BOOLEAN` | 100.0% |
| `qty` | `BIGINT` | 18.7% |
| `method` | `VARCHAR` | 100.0% |
| `signal` | `VARCHAR` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (475 rows).

> **2 columns never populated:** `state`, `upc`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `hemp_inventory.py:127` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
