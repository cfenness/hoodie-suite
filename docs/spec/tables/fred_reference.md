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

| column | type |
|---|---|
| `dataset` | `VARCHAR` |
| `series_id` | `VARCHAR` |
| `series_name` | `VARCHAR` |
| `date` | `VARCHAR` |
| `vintage_year` | `BIGINT` |
| `period` | `VARCHAR` |
| `metric_value` | `DOUBLE` |
| `unit` | `VARCHAR` |
| `source_pulled_at` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `fred_ref.py:101` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
