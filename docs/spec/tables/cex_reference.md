# `cex_reference`

|  |  |
|---|---|
| Status | landed |
| Rows | 450 |
| Columns | 13 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `cex` |
| URI | `s3://hoodie-suite-warehouse/warehouse/cex_reference.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `dataset` | `VARCHAR` | 100.0% |
| `vintage_year` | `BIGINT` | 100.0% |
| `item_code` | `VARCHAR` | 100.0% |
| `item_name` | `VARCHAR` | 100.0% |
| `demographic` | `VARCHAR` | 100.0% |
| `bracket_code` | `VARCHAR` | 100.0% |
| `bracket_label` | `VARCHAR` | 100.0% |
| `bracket_lo` | `DOUBLE` | 100.0% |
| `bracket_hi` | `DOUBLE` | 80.0% |
| `metric_name` | `VARCHAR` | 100.0% |
| `metric_value` | `DOUBLE` | 100.0% |
| `suppressed` | `BOOLEAN` | 100.0% |
| `source_pulled_at` | `BIGINT` | 100.0% |

Fill measured over **full table** (450 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `cex_ref.py:195` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
