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

| column | type | filled |
|---|---|---|
| `path_key` | `VARCHAR` | 100.0% |
| `canon_type` | `VARCHAR` | 100.0% |
| `canon_class` | `VARCHAR` | 100.0% |
| `canon_subclass` | `VARCHAR` | 100.0% |
| `canon_varietal` | `VARCHAR` | 100.0% |
| `times` | `BIGINT` | 100.0% |
| `first_seen` | `VARCHAR` | 100.0% |
| `last_seen` | `VARCHAR` | 100.0% |

Fill measured over **full table** (2 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `product_taxonomy.py:551` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
