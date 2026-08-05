# `normalization_findings`

|  |  |
|---|---|
| Status | landed |
| Rows | 234 |
| Columns | 11 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/normalization_findings.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `table` | `VARCHAR` | 100.0% |
| `field` | `VARCHAR` | 100.0% |
| `check` | `VARCHAR` | 100.0% |
| `kind` | `VARCHAR` | 100.0% |
| `cohort_n` | `BIGINT` | 100.0% |
| `total_n` | `BIGINT` | 100.0% |
| `share` | `DOUBLE` | 100.0% |
| `evidence` | `VARCHAR` | 100.0% |
| `proposal` | `VARCHAR` | 100.0% |
| `samples` | `VARCHAR` | 100.0% |
| `found_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (234 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalization_scout.py:272` | `write_parquet` | flat (full overwrite) | no |
