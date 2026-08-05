# `xsource_gold`

|  |  |
|---|---|
| Status | landed |
| Rows | 6 |
| Columns | 31 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `xsource-gold` |
| URI | `s3://hoodie-suite-warehouse/warehouse/xsource_gold.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `pair_id` | `VARCHAR` | 100.0% |
| `stratum` | `VARCHAR` | 100.0% |
| `a_id` | `VARCHAR` | 100.0% |
| `a_source` | `VARCHAR` | 100.0% |
| `a_brand` | `VARCHAR` | 83.3% |
| `a_name` | `VARCHAR` | 100.0% |
| `a_size` | `VARCHAR` | 83.3% |
| `a_upc` | `VARCHAR` | 33.3% |
| `b_id` | `VARCHAR` | 100.0% |
| `b_source` | `VARCHAR` | 100.0% |
| `b_brand` | `VARCHAR` | 83.3% |
| `b_name` | `VARCHAR` | 100.0% |
| `b_size` | `VARCHAR` | 83.3% |
| `b_upc` | `VARCHAR` | 16.7% |
| `rule_merges` | `BOOLEAN` | 100.0% |
| `suggested` | `VARCHAR` | **0%** ‹never populated› |
| `suggest_reason` | `VARCHAR` | **0%** ‹never populated› |
| `label` | `VARCHAR` | 66.7% |
| `labelled_by` | `VARCHAR` | **0%** ‹never populated› |
| `labelled_at` | `VARCHAR` | 100.0% |
| `canon_brand` | `VARCHAR` | 16.7% |
| `canon_product` | `VARCHAR` | 16.7% |
| `canon_size` | `VARCHAR` | 16.7% |
| `canon_category` | `INTEGER` | **0%** ‹never populated› |
| `canon_type` | `VARCHAR` | 16.7% |
| `canon_class` | `VARCHAR` | 16.7% |
| `canon_subclass` | `VARCHAR` | 16.7% |
| `canon_varietal` | `VARCHAR` | 16.7% |
| `annotations` | `INTEGER` | **0%** ‹never populated› |
| `sample_seed` | `BIGINT` | 100.0% |
| `built_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (6 rows).

> **5 columns never populated:** `suggested`, `suggest_reason`, `labelled_by`, `canon_category`, `annotations`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `xsource_gold.py:383` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `xsource_gold.py:430` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
