# `census_acs`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,498,570 |
| Columns | 10 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `census-acs5` |
| URI | `s3://hoodie-suite-warehouse/warehouse/census_acs.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `dataset` | `VARCHAR` | 100.0% |
| `vintage_year` | `BIGINT` | 100.0% |
| `table_id` | `VARCHAR` | 100.0% |
| `variable` | `VARCHAR` | 100.0% |
| `label` | `VARCHAR` | 100.0% |
| `geo_level` | `VARCHAR` | 100.0% |
| `geo_fips` | `VARCHAR` | 100.0% |
| `estimate` | `DOUBLE` | 83.9% |
| `suppressed` | `BOOLEAN` | 100.0% |
| `source_pulled_at` | `BIGINT` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `census_ref.py:384` | `write_parquet` | flat (full overwrite) | no |
