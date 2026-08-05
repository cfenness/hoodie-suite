# `item_identity`

|  |  |
|---|---|
| Status | landed |
| Rows | 147,235 |
| Columns | 14 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-item-identity` |
| URI | `s3://hoodie-suite-warehouse/warehouse/item_identity.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `canon_item_id` | `BIGINT` | 100.0% |
| `brand` | `VARCHAR` | 58.9% |
| `product_name` | `VARCHAR` | 99.7% |
| `category` | `VARCHAR` | 26.1% |
| `size_ml` | `DOUBLE` | 9.9% |
| `size_raw` | `VARCHAR` | **0.7%** |
| `upcs` | `VARCHAR[]` | 100.0% |
| `identity_key` | `VARCHAR` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `merged_into` | `INTEGER` | **0%** ‹never populated› |
| `match_tier` | `BIGINT` | 100.0% |
| `method_version` | `VARCHAR` | 100.0% |

Fill measured over **full table** (147,235 rows).

> **1 column never populated:** `merged_into`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_item_identity.py:105` | `write_parquet` | flat (full overwrite) | no |
| `ingest_canon_identity.py:55` | `write_parquet` | flat (full overwrite) | no |
