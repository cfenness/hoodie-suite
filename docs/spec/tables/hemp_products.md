# `hemp_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 4,040 |
| Columns | 11 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `hemp-scan` |
| URI | `s3://hoodie-suite-warehouse/warehouse/hemp_products.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `category` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `size_ml` | `VARCHAR` |
| `price` | `VARCHAR` |
| `image` | `VARCHAR` |
| `state` | `VARCHAR` |
| `url` | `VARCHAR` |
| `signal` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `hemp_scan.py:120` | `write_parquet` | flat (full overwrite) | no |
