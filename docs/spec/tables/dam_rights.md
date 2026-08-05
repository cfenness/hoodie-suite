# `dam_rights`

|  |  |
|---|---|
| Status | landed |
| Rows | 1 |
| Columns | 27 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/dam_rights.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source_id` | `VARCHAR` | 100.0% |
| `vendor` | `VARCHAR` | 100.0% |
| `host` | `VARCHAR` | 100.0% |
| `tos_url` | `VARCHAR` | 100.0% |
| `tos_sha256` | `VARCHAR` | 100.0% |
| `tos_captured_at` | `VARCHAR` | 100.0% |
| `tos_capture_method` | `VARCHAR` | 100.0% |
| `robots_sha256` | `VARCHAR` | 100.0% |
| `robots_checked_at` | `VARCHAR` | 100.0% |
| `robots_allows_harvest` | `BOOLEAN` | 100.0% |
| `facts_use` | `VARCHAR` | 100.0% |
| `image_use` | `VARCHAR` | 100.0% |
| `scope` | `VARCHAR` | 100.0% |
| `attribution_required` | `BOOLEAN` | 100.0% |
| `alteration_allowed` | `INTEGER` | **0%** ‹never populated› |
| `expiry` | `INTEGER` | **0%** ‹never populated› |
| `confidence` | `VARCHAR` | 100.0% |
| `needs_counsel` | `BOOLEAN` | 100.0% |
| `counsel_cleared` | `BOOLEAN` | 100.0% |
| `review_state` | `VARCHAR` | 100.0% |
| `schema_version` | `VARCHAR` | 100.0% |
| `schema_signoff` | `INTEGER` | **0%** ‹never populated› |
| `reviewed_by` | `VARCHAR` | 100.0% |
| `reviewed_at` | `VARCHAR` | 100.0% |
| `escalation` | `VARCHAR` | 100.0% |
| `evidence_json` | `VARCHAR` | 100.0% |
| `landed_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (1 rows).

> **3 columns never populated:** `alteration_allowed`, `expiry`, `schema_signoff`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `rights.py:609` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
