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

| column | type |
|---|---|
| `VPID` | `VARCHAR` |
| `Name` | `VARCHAR` |
| `Address` | `VARCHAR` |
| `City` | `VARCHAR` |
| `State` | `VARCHAR` |
| `Zip` | `VARCHAR` |
| `Lat` | `DOUBLE` |
| `Lng` | `DOUBLE` |
| `AB_Brands` | `VARCHAR` |
| `Zips_Hit` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `ab_fill.py:68` | `write_parquet` | flat (full overwrite) | no |
