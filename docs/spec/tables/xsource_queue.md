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

| column | type |
|---|---|
| `pair_id` | `VARCHAR` |
| `stratum` | `VARCHAR` |
| `a_id` | `VARCHAR` |
| `a_source` | `VARCHAR` |
| `a_brand` | `VARCHAR` |
| `a_name` | `VARCHAR` |
| `a_size` | `VARCHAR` |
| `a_upc` | `VARCHAR` |
| `b_id` | `VARCHAR` |
| `b_source` | `VARCHAR` |
| `b_brand` | `VARCHAR` |
| `b_name` | `VARCHAR` |
| `b_size` | `VARCHAR` |
| `b_upc` | `VARCHAR` |
| `rule_merges` | `BOOLEAN` |
| `suggested` | `VARCHAR` |
| `suggest_reason` | `VARCHAR` |
| `label` | `VARCHAR` |
| `labelled_by` | `VARCHAR` |
| `labelled_at` | `VARCHAR` |
| `canon_brand` | `VARCHAR` |
| `canon_product` | `VARCHAR` |
| `canon_size` | `VARCHAR` |
| `canon_category` | `INTEGER` |
| `canon_type` | `VARCHAR` |
| `canon_class` | `VARCHAR` |
| `canon_subclass` | `VARCHAR` |
| `canon_varietal` | `VARCHAR` |
| `annotations` | `INTEGER` |
| `sample_seed` | `BIGINT` |
| `built_at` | `VARCHAR` |
| `priority` | `BIGINT` |
| `resolved` | `VARCHAR` |
| `queued_at` | `VARCHAR` |
| `difference` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `xsource_queue.py:278` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `xsource_queue.py:198` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
