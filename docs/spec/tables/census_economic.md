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

| column | type |
|---|---|
| `name` | `VARCHAR` |
| `median_household_income` | `VARCHAR` |
| `per_capita_income` | `VARCHAR` |
| `poverty_pop` | `VARCHAR` |
| `labor_force` | `VARCHAR` |
| `unemployed` | `VARCHAR` |
| `state_fips` | `VARCHAR` |
| `county_fips` | `VARCHAR` |
| `geoid` | `VARCHAR` |
