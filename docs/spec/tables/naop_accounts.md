# `naop_accounts`

|  |  |
|---|---|
| Status | landed |
| Rows | 4,794 |
| Columns | 13 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `naop` |
| URI | `s3://hoodie-suite-warehouse/warehouse/naop_accounts.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `account` | `VARCHAR` | 100.0% |
| `clean_name` | `VARCHAR` | 96.0% |
| `street` | `VARCHAR` | 95.9% |
| `city` | `VARCHAR` | 96.0% |
| `state` | `VARCHAR` | 96.0% |
| `phone` | `VARCHAR` | **0%** ‹never populated› |
| `cuisine` | `VARCHAR` | 73.7% |
| `cuisines` | `VARCHAR` | 65.4% |
| `cuisine_source` | `VARCHAR` | 100.0% |
| `serves_alcohol` | `BOOLEAN` | 100.0% |
| `n_beverages` | `BIGINT` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |

Fill measured over **full table** (4,794 rows).

> **1 column never populated:** `phone`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `doordash_naop.py:195` | `write_parquet` | flat (full overwrite) | no |
