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

| column | type | filled |
|---|---|---|
| `name` | `VARCHAR` | 100.0% |
| `population` | `VARCHAR` | 100.0% |
| `median_age` | `VARCHAR` | 100.0% |
| `households` | `VARCHAR` | 100.0% |
| `hispanic_pop` | `VARCHAR` | 100.0% |
| `white_pop` | `VARCHAR` | 100.0% |
| `black_pop` | `VARCHAR` | 100.0% |
| `asian_pop` | `VARCHAR` | 100.0% |
| `state_fips` | `VARCHAR` | 100.0% |
| `county_fips` | `VARCHAR` | 100.0% |
| `geoid` | `VARCHAR` | 100.0% |

Fill measured over **full table** (423 rows).