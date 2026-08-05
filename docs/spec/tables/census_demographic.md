# `census_demographic`

|  |  |
|---|---|
| Status | landed |
| Rows | 423 |
| Columns | 11 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `census-acs` |
| URI | `s3://hoodie-suite-warehouse/warehouse/census_demographic.parquet` |


## Columns

| column | type |
|---|---|
| `name` | `VARCHAR` |
| `population` | `VARCHAR` |
| `median_age` | `VARCHAR` |
| `households` | `VARCHAR` |
| `hispanic_pop` | `VARCHAR` |
| `white_pop` | `VARCHAR` |
| `black_pop` | `VARCHAR` |
| `asian_pop` | `VARCHAR` |
| `state_fips` | `VARCHAR` |
| `county_fips` | `VARCHAR` |
| `geoid` | `VARCHAR` |
