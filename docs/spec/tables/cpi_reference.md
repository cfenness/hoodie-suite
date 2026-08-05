# `cpi_reference`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,830 |
| Columns | 10 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `cpi` |
| URI | `s3://hoodie-suite-warehouse/warehouse/cpi_reference.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `dataset` | `VARCHAR` | 100.0% |
| `series_id` | `VARCHAR` | 100.0% |
| `item_code` | `VARCHAR` | 100.0% |
| `item_name` | `VARCHAR` | 100.0% |
| `area_code` | `VARCHAR` | 100.0% |
| `area_name` | `VARCHAR` | 100.0% |
| `vintage_year` | `BIGINT` | 100.0% |
| `period` | `VARCHAR` | 100.0% |
| `metric_value` | `DOUBLE` | 100.0% |
| `source_pulled_at` | `BIGINT` | 100.0% |

Fill measured over **full table** (1,830 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `cpi_ref.py:142` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
