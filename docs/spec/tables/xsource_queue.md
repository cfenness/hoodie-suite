# `xsource_queue`

|  |  |
|---|---|
| Status | landed |
| Rows | 4,617 |
| Columns | 35 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `xsource-queue` |
| URI | `s3://hoodie-suite-warehouse/warehouse/xsource_queue.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `pair_id` | `VARCHAR` | 100.0% |
| `stratum` | `VARCHAR` | 100.0% |
| `a_id` | `VARCHAR` | 100.0% |
| `a_source` | `VARCHAR` | 100.0% |
| `a_brand` | `VARCHAR` | 96.6% |
| `a_name` | `VARCHAR` | 100.0% |
| `a_size` | `VARCHAR` | 75.4% |
| `a_upc` | `VARCHAR` | 16.4% |
| `b_id` | `VARCHAR` | 100.0% |
| `b_source` | `VARCHAR` | 100.0% |
| `b_brand` | `VARCHAR` | 96.7% |
| `b_name` | `VARCHAR` | 100.0% |
| `b_size` | `VARCHAR` | 71.3% |
| `b_upc` | `VARCHAR` | 17.4% |
| `rule_merges` | `BOOLEAN` | 100.0% |
| `suggested` | `VARCHAR` | **0%** ‹never populated› |
| `suggest_reason` | `VARCHAR` | **0%** ‹never populated› |
| `label` | `VARCHAR` | **0.1%** |
| `labelled_by` | `VARCHAR` | **0%** ‹never populated› |
| `labelled_at` | `VARCHAR` | **0.1%** |
| `canon_brand` | `VARCHAR` | **0%** ‹never populated› |
| `canon_product` | `VARCHAR` | **0%** ‹never populated› |
| `canon_size` | `VARCHAR` | **0%** ‹never populated› |
| `canon_category` | `INTEGER` | **0%** ‹never populated› |
| `canon_type` | `VARCHAR` | **0%** ‹never populated› |
| `canon_class` | `VARCHAR` | **0%** ‹never populated› |
| `canon_subclass` | `VARCHAR` | **0%** ‹never populated› |
| `canon_varietal` | `VARCHAR` | **0%** ‹never populated› |
| `annotations` | `INTEGER` | **0%** ‹never populated› |
| `sample_seed` | `BIGINT` | 100.0% |
| `built_at` | `VARCHAR` | 100.0% |
| `priority` | `BIGINT` | 100.0% |
| `resolved` | `VARCHAR` | **0.1%** |
| `queued_at` | `VARCHAR` | 100.0% |
| `difference` | `VARCHAR` | 100.0% |

Fill measured over **full table** (4,617 rows).

> **12 columns never populated:** `suggested`, `suggest_reason`, `labelled_by`, `canon_brand`, `canon_product`, `canon_size`, `canon_category`, `canon_type`, `canon_class`, `canon_subclass`, `canon_varietal`, `annotations`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `xsource_queue.py:278` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `xsource_queue.py:198` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
