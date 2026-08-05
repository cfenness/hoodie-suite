# `xsource_taxonomy`

|  |  |
|---|---|
| Status | landed |
| Rows | 2 |
| Columns | 8 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/xsource_taxonomy.parquet` |


## Columns

| column | type |
|---|---|
| `path_key` | `VARCHAR` |
| `canon_type` | `VARCHAR` |
| `canon_class` | `VARCHAR` |
| `canon_subclass` | `VARCHAR` |
| `canon_varietal` | `VARCHAR` |
| `times` | `BIGINT` |
| `first_seen` | `VARCHAR` |
| `last_seen` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `product_taxonomy.py:551` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
