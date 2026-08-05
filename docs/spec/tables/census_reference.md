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

| column | type | filled |
|---|---|---|
| `dataset` | `VARCHAR` | 100.0% |
| `vintage_year` | `BIGINT` | 100.0% |
| `naics_code` | `VARCHAR` | 5.3% |
| `geo_level` | `VARCHAR` | 100.0% |
| `geo_fips` | `VARCHAR` | 100.0% |
| `metric_name` | `VARCHAR` | 100.0% |
| `metric_value` | `DOUBLE` | 99.4% |
| `suppressed` | `BOOLEAN` | 100.0% |
| `source_pulled_at` | `BIGINT` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `census_ref.py:371` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
