# `census_housing`

|  |  |
|---|---|
| Status | landed |
| Rows | 423 |
| Columns | 9 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `census-acs` |
| URI | `s3://hoodie-suite-warehouse/warehouse/census_housing.parquet` |


## Columns

| column | type |
|---|---|
| `name` | `VARCHAR` |
| `median_home_value` | `VARCHAR` |
| `median_gross_rent` | `VARCHAR` |
| `housing_units` | `VARCHAR` |
| `owner_occupied` | `VARCHAR` |
| `renter_occupied` | `VARCHAR` |
| `state_fips` | `VARCHAR` |
| `county_fips` | `VARCHAR` |
| `geoid` | `VARCHAR` |
