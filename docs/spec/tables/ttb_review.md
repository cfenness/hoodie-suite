# `ttb_review`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,462 |
| Columns | 18 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/ttb_review.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `cluster_id` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `fanciful` | `VARCHAR` | **0%** ‹never populated› |
| `class_type` | `VARCHAR` | 100.0% |
| `size_ml` | `VARCHAR` | **0%** ‹never populated› |
| `supplier` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 42.4% |
| `corroborated_by` | `VARCHAR` | 100.0% |
| `confidence` | `DOUBLE` | 100.0% |
| `match_kind` | `VARCHAR` | 100.0% |
| `size_matched` | `BOOLEAN` | 100.0% |
| `candidate_name` | `VARCHAR` | 100.0% |
| `matched_by` | `VARCHAR` | 100.0% |
| `member_count` | `BIGINT` | 100.0% |
| `members` | `VARCHAR` | 100.0% |
| `first_day` | `BIGINT` | 100.0% |
| `last_day` | `BIGINT` | 100.0% |
| `tier` | `BIGINT` | 100.0% |

Fill measured over **full table** (1,462 rows).

> **2 columns never populated:** `fanciful`, `size_ml`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `master_ttb.py:134` | `write_parquet` | flat (full overwrite) | no |
