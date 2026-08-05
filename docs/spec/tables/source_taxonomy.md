# `source_taxonomy`

|  |  |
|---|---|
| Status | landed |
| Rows | 10,825 |
| Columns | 3 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `abc-facets` |
| URI | `s3://hoodie-suite-warehouse/warehouse/source_taxonomy.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `axis` | `VARCHAR` | 100.0% |
| `value` | `VARCHAR` | 100.0% |

Fill measured over **full table** (10,825 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `abc_facets.py:120` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
