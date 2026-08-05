# `ca_outlets`

|  |  |
|---|---|
| Status | landed |
| Rows | 128,950 |
| Columns | 26 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `ca-abc` |
| URI | `s3://hoodie-suite-warehouse/warehouse/ca_outlets.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `License Type` | `VARCHAR` | 100.0% |
| `File Number` | `VARCHAR` | 100.0% |
| `Lic or App` | `VARCHAR` | 100.0% |
| `Type Status` | `VARCHAR` | 100.0% |
| `Type Orig Iss Date` | `VARCHAR` | 84.4% |
| `Expir Date` | `VARCHAR` | 84.4% |
| `Fee Codes` | `VARCHAR` | 99.9% |
| `Dup Counts` | `VARCHAR` | 14.7% |
| `Master Ind` | `VARCHAR` | 100.0% |
| `Term in # of Months` | `VARCHAR` | 100.0% |
| `Geo Code` | `VARCHAR` | 99.9% |
| `District` | `VARCHAR` | 100.0% |
| `Primary Name` | `VARCHAR` | 99.7% |
| `Prem Addr 1` | `VARCHAR` | 99.9% |
| `Prem Addr 2` | `VARCHAR` | 20.3% |
| `Prem City` | `VARCHAR` | 99.8% |
| `Prem State` | `VARCHAR` | 99.8% |
| `Prem Zip` | `VARCHAR` | 99.8% |
| `DBA Name` | `VARCHAR` | 94.0% |
| `Mail Addr 1` | `VARCHAR` | 59.7% |
| `Mail Addr 2` | `VARCHAR` | 22.3% |
| `Mail City` | `VARCHAR` | 59.7% |
| `Mail State` | `VARCHAR` | 59.7% |
| `Mail Zip` | `VARCHAR` | 59.7% |
| `Prem County` | `VARCHAR` | 98.2% |
| `Prem Census Tract #` | `VARCHAR` | 97.4% |

Fill measured over **full table** (128,950 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `ca_abc.py:46` | `write_parquet` | flat (full overwrite) | no |
