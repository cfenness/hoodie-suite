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

| column | type |
|---|---|
| `dataset` | `VARCHAR` |
| `vintage_year` | `BIGINT` |
| `table_id` | `VARCHAR` |
| `variable` | `VARCHAR` |
| `label` | `VARCHAR` |
| `geo_level` | `VARCHAR` |
| `geo_fips` | `VARCHAR` |
| `estimate` | `DOUBLE` |
| `suppressed` | `BOOLEAN` |
| `source_pulled_at` | `BIGINT` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `census_ref.py:384` | `write_parquet` | flat (full overwrite) | no |
