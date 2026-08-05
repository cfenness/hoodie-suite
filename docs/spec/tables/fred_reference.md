# `fred_reference`

|  |  |
|---|---|
| Status | landed |
| Rows | 550 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `fred` |
| URI | `s3://hoodie-suite-warehouse/warehouse/fred_reference.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `dataset` | `VARCHAR` | 100.0% |
| `series_id` | `VARCHAR` | 100.0% |
| `series_name` | `VARCHAR` | 100.0% |
| `date` | `VARCHAR` | 100.0% |
| `vintage_year` | `BIGINT` | 100.0% |
| `period` | `VARCHAR` | 100.0% |
| `metric_value` | `DOUBLE` | 100.0% |
| `unit` | `VARCHAR` | 100.0% |
| `source_pulled_at` | `BIGINT` | 100.0% |

Fill measured over **full table** (550 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `fred_ref.py:101` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
