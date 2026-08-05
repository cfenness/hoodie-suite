# `cex_reference`

|  |  |
|---|---|
| Status | landed |
| Rows | 450 |
| Columns | 13 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `cex` |
| URI | `s3://hoodie-suite-warehouse/warehouse/cex_reference.parquet` |


## Columns

| column | type |
|---|---|
| `dataset` | `VARCHAR` |
| `vintage_year` | `BIGINT` |
| `item_code` | `VARCHAR` |
| `item_name` | `VARCHAR` |
| `demographic` | `VARCHAR` |
| `bracket_code` | `VARCHAR` |
| `bracket_label` | `VARCHAR` |
| `bracket_lo` | `DOUBLE` |
| `bracket_hi` | `DOUBLE` |
| `metric_name` | `VARCHAR` |
| `metric_value` | `DOUBLE` |
| `suppressed` | `BOOLEAN` |
| `source_pulled_at` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `cex_ref.py:195` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
