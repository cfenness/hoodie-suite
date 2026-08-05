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

| column | type | filled |
|---|---|---|
| `dataset` | `VARCHAR` | 100.0% |
| `table_name` | `VARCHAR` | 100.0% |
| `line_code` | `VARCHAR` | 100.0% |
| `metric_name` | `VARCHAR` | 100.0% |
| `geo_level` | `VARCHAR` | 100.0% |
| `geo_fips` | `VARCHAR` | 100.0% |
| `vintage_year` | `BIGINT` | 100.0% |
| `metric_value` | `DOUBLE` | 100.0% |
| `unit` | `VARCHAR` | 100.0% |
| `source_pulled_at` | `BIGINT` | 100.0% |

Fill measured over **full table** (96,450 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `bea_ref.py:124` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
