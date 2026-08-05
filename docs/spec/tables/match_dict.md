# `match_dict`

|  |  |
|---|---|
| Status | landed |
| Rows | 400 |
| Columns | 4 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated), flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/match_dict.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `kind` | `VARCHAR` | 100.0% |
| `match` | `VARCHAR` | 100.0% |
| `value` | `VARCHAR` | 84.2% |
| `mode` | `VARCHAR` | 100.0% |

Fill measured over **full table** (400 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dict_apply.py:129` | `write_parquet` | flat (full overwrite) | no |
| `dict_apply.py:165` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
