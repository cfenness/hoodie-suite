# `ab_outlets`

|  |  |
|---|---|
| Status | landed |
| Rows | 278,510 |
| Columns | 10 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `ab-inbev` |
| URI | `s3://hoodie-suite-warehouse/warehouse/ab_outlets.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `VPID` | `VARCHAR` | 100.0% |
| `Name` | `VARCHAR` | 100.0% |
| `Address` | `VARCHAR` | 100.0% |
| `City` | `VARCHAR` | 100.0% |
| `State` | `VARCHAR` | 100.0% |
| `Zip` | `VARCHAR` | 100.0% |
| `Lat` | `DOUBLE` | 100.0% |
| `Lng` | `DOUBLE` | 100.0% |
| `AB_Brands` | `VARCHAR` | 100.0% |
| `Zips_Hit` | `VARCHAR` | 100.0% |

Fill measured over **full table** (278,510 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `ab_fill.py:68` | `write_parquet` | flat (full overwrite) | no |
