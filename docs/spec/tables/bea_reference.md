# `bea_reference`

|  |  |
|---|---|
| Status | landed |
| Rows | 96,450 |
| Columns | 10 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `bea` |
| URI | `s3://hoodie-suite-warehouse/warehouse/bea_reference.parquet` |


## Columns

| column | type |
|---|---|
| `dataset` | `VARCHAR` |
| `table_name` | `VARCHAR` |
| `line_code` | `VARCHAR` |
| `metric_name` | `VARCHAR` |
| `geo_level` | `VARCHAR` |
| `geo_fips` | `VARCHAR` |
| `vintage_year` | `BIGINT` |
| `metric_value` | `DOUBLE` |
| `unit` | `VARCHAR` |
| `source_pulled_at` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `bea_ref.py:124` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
