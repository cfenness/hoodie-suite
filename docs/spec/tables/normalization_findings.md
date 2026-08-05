# `normalization_findings`

|  |  |
|---|---|
| Status | landed |
| Rows | 234 |
| Columns | 11 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/normalization_findings.parquet` |


## Columns

| column | type |
|---|---|
| `table` | `VARCHAR` |
| `field` | `VARCHAR` |
| `check` | `VARCHAR` |
| `kind` | `VARCHAR` |
| `cohort_n` | `BIGINT` |
| `total_n` | `BIGINT` |
| `share` | `DOUBLE` |
| `evidence` | `VARCHAR` |
| `proposal` | `VARCHAR` |
| `samples` | `VARCHAR` |
| `found_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalization_scout.py:272` | `write_parquet` | flat (full overwrite) | no |
