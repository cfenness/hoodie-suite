# `census_housing`

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
| URI | `s3://hoodie-suite-warehouse/warehouse/census_housing.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `name` | `VARCHAR` | 100.0% |
| `median_home_value` | `VARCHAR` | 99.1% |
| `median_gross_rent` | `VARCHAR` | 98.1% |
| `housing_units` | `VARCHAR` | 100.0% |
| `owner_occupied` | `VARCHAR` | 100.0% |
| `renter_occupied` | `VARCHAR` | 100.0% |
| `state_fips` | `VARCHAR` | 100.0% |
| `county_fips` | `VARCHAR` | 100.0% |
| `geoid` | `VARCHAR` | 100.0% |

Fill measured over **full table** (423 rows).