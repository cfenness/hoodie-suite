# `ca_outlets`

|  |  |
|---|---|
| Status | landed |
| Rows | 128,950 |
| Columns | 26 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `ca-abc` |
| URI | `s3://hoodie-suite-warehouse/warehouse/ca_outlets.parquet` |


## Columns

| column | type |
|---|---|
| `License Type` | `VARCHAR` |
| `File Number` | `VARCHAR` |
| `Lic or App` | `VARCHAR` |
| `Type Status` | `VARCHAR` |
| `Type Orig Iss Date` | `VARCHAR` |
| `Expir Date` | `VARCHAR` |
| `Fee Codes` | `VARCHAR` |
| `Dup Counts` | `VARCHAR` |
| `Master Ind` | `VARCHAR` |
| `Term in # of Months` | `VARCHAR` |
| `Geo Code` | `VARCHAR` |
| `District` | `VARCHAR` |
| `Primary Name` | `VARCHAR` |
| `Prem Addr 1` | `VARCHAR` |
| `Prem Addr 2` | `VARCHAR` |
| `Prem City` | `VARCHAR` |
| `Prem State` | `VARCHAR` |
| `Prem Zip` | `VARCHAR` |
| `DBA Name` | `VARCHAR` |
| `Mail Addr 1` | `VARCHAR` |
| `Mail Addr 2` | `VARCHAR` |
| `Mail City` | `VARCHAR` |
| `Mail State` | `VARCHAR` |
| `Mail Zip` | `VARCHAR` |
| `Prem County` | `VARCHAR` |
| `Prem Census Tract #` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `ca_abc.py:46` | `write_parquet` | flat (full overwrite) | no |
