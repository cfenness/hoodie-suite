# `dam_census`

|  |  |
|---|---|
| Status | landed |
| Rows | 67 |
| Columns | 26 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `dam-census` |
| URI | `s3://hoodie-suite-warehouse/warehouse/dam_census.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `supplier` | `VARCHAR` | 100.0% |
| `corporate_domain` | `VARCHAR` | 100.0% |
| `media_url` | `VARCHAR` | 82.1% |
| `media_host` | `VARCHAR` | 82.1% |
| `dam_vendor` | `VARCHAR` | 7.5% |
| `vendor_signals` | `VARCHAR` | 9.0% |
| `vendor_confidence` | `VARCHAR` | 9.0% |
| `kind` | `VARCHAR` | 47.8% |
| `public` | `BOOLEAN` | 32.8% |
| `drive_id` | `INTEGER` | **0%** ‹never populated› |
| `company_id` | `INTEGER` | **0%** ‹never populated› |
| `reachable` | `BOOLEAN` | 100.0% |
| `http_status` | `BIGINT` | 95.5% |
| `robots_allows` | `BOOLEAN` | 88.1% |
| `tos_url` | `VARCHAR` | 53.7% |
| `tos_chars` | `BIGINT` | 70.1% |
| `tos_capture` | `VARCHAR` | 70.1% |
| `image_use` | `VARCHAR` | 44.8% |
| `scope` | `VARCHAR` | 44.8% |
| `confidence` | `VARCHAR` | 44.8% |
| `needs_counsel` | `BOOLEAN` | 70.1% |
| `provisional` | `BOOLEAN` | 100.0% |
| `discovery_method` | `VARCHAR` | 100.0% |
| `connector` | `VARCHAR` | **1.5%** |
| `notes` | `VARCHAR` | 95.5% |
| `checked_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (67 rows).

> **2 columns never populated:** `drive_id`, `company_id`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dam_census.py:654` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
