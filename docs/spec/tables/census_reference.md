# `census_reference`

|  |  |
|---|---|
| Status | landed |
| Rows | 876,266 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `census` |
| URI | `s3://hoodie-suite-warehouse/warehouse/census_reference.parquet` |


## Columns

| column | type |
|---|---|
| `dataset` | `VARCHAR` |
| `vintage_year` | `BIGINT` |
| `naics_code` | `VARCHAR` |
| `geo_level` | `VARCHAR` |
| `geo_fips` | `VARCHAR` |
| `metric_name` | `VARCHAR` |
| `metric_value` | `DOUBLE` |
| `suppressed` | `BOOLEAN` |
| `source_pulled_at` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `census_ref.py:371` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
