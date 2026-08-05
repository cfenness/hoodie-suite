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

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `axis` | `VARCHAR` |
| `value` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `abc_facets.py:120` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
