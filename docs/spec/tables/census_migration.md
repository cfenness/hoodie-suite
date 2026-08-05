# `census_migration`

|  |  |
|---|---|
| Status | landed |
| Rows | 126,011 |
| Columns | 8 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `census-migration` |
| URI | `s3://hoodie-suite-warehouse/warehouse/census_migration.parquet` |


## Columns

| column | type |
|---|---|
| `vintage_year` | `BIGINT` |
| `geo_fips` | `VARCHAR` |
| `geo_name` | `VARCHAR` |
| `other_fips` | `VARCHAR` |
| `other_name` | `VARCHAR` |
| `metric_name` | `VARCHAR` |
| `metric_value` | `DOUBLE` |
| `source_pulled_at` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `census_ref.py:395` | `write_parquet` | flat (full overwrite) | no |
