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

| column | type |
|---|---|
| `dataset` | `VARCHAR` |
| `series_id` | `VARCHAR` |
| `item_code` | `VARCHAR` |
| `item_name` | `VARCHAR` |
| `area_code` | `VARCHAR` |
| `area_name` | `VARCHAR` |
| `vintage_year` | `BIGINT` |
| `period` | `VARCHAR` |
| `metric_value` | `DOUBLE` |
| `source_pulled_at` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `cpi_ref.py:142` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
