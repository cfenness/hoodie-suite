# `census_economic`

|  |  |
|---|---|
| Status | landed |
| Rows | 423 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `census-acs` |
| URI | `s3://hoodie-suite-warehouse/warehouse/census_economic.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `name` | `VARCHAR` | 100.0% |
| `median_household_income` | `VARCHAR` | 99.8% |
| `per_capita_income` | `VARCHAR` | 100.0% |
| `poverty_pop` | `VARCHAR` | 100.0% |
| `labor_force` | `VARCHAR` | 100.0% |
| `unemployed` | `VARCHAR` | 100.0% |
| `state_fips` | `VARCHAR` | 100.0% |
| `county_fips` | `VARCHAR` | 100.0% |
| `geoid` | `VARCHAR` | 100.0% |

Fill measured over **full table** (423 rows).