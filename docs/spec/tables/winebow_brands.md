# `winebow_brands`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,396 |
| Columns | 7 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `winebow` |
| URI | `s3://hoodie-suite-warehouse/warehouse/winebow_brands.parquet` |


## Columns

| column | type |
|---|---|
| `brand` | `VARCHAR` |
| `website` | `VARCHAR` |
| `logo` | `VARCHAR` |
| `importer` | `VARCHAR` |
| `country` | `VARCHAR` |
| `product_type` | `VARCHAR` |
| `source` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `winebow.py:86` | `write_parquet` | flat (full overwrite) | no |
