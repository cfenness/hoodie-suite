# `outlet_master`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,818,275 |
| Columns | 18 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `outlet-union` |
| URI | `s3://hoodie-suite-warehouse/warehouse/outlet_master.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `outlet_id` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `street` | `VARCHAR` | **0.2%** |
| `city` | `VARCHAR` | 100.0% |
| `state` | `VARCHAR` | 99.9% |
| `phone` | `VARCHAR` | **0%** ‹never populated› |
| `lat` | `DOUBLE` | **0%** ‹never populated› |
| `lng` | `DOUBLE` | **0%** ‹never populated› |
| `sources` | `VARCHAR` | 100.0% |
| `source_count` | `BIGINT` | 100.0% |
| `doordash_id` | `VARCHAR` | 100.0% |
| `toast_guid` | `VARCHAR` | **0%** ‹never populated› |
| `ubereats_id` | `VARCHAR` | **0%** ‹never populated› |
| `doordash_menu_date` | `VARCHAR` | **0.3%** |
| `toast_menu_date` | `VARCHAR` | **0%** ‹never populated› |
| `ubereats_menu_date` | `VARCHAR` | **0%** ‹never populated› |
| `freshest_source` | `VARCHAR` | **0.3%** |
| `freshest_date` | `VARCHAR` | **0.3%** |

Fill measured over **first 400,000 rows** (400,000 rows).

> **7 columns never populated:** `phone`, `lat`, `lng`, `toast_guid`, `ubereats_id`, `toast_menu_date`, `ubereats_menu_date`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `outlet_union.py:137` | `write_parquet` | flat (full overwrite) | no |
