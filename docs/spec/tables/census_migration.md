# `census_migration`

|  |  |
|---|---|
| Status | landed |
| Rows | 126,011 |
| Columns | 8 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `census-migration` |
| URI | `s3://hoodie-suite-warehouse/warehouse/census_migration.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `vintage_year` | `BIGINT` | 100.0% |
| `geo_fips` | `VARCHAR` | 100.0% |
| `geo_name` | `VARCHAR` | 100.0% |
| `other_fips` | `VARCHAR` | 100.0% |
| `other_name` | `VARCHAR` | 100.0% |
| `metric_name` | `VARCHAR` | 100.0% |
| `metric_value` | `DOUBLE` | 100.0% |
| `source_pulled_at` | `BIGINT` | 100.0% |

Fill measured over **full table** (126,011 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `census_ref.py:395` | `write_parquet` | flat (full overwrite) | no |
