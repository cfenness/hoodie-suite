# `abc_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 9,399 |
| Columns | 21 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `abc-facets` |
| URI | `s3://hoodie-suite-warehouse/warehouse/abc_products.parquet` |


## Columns

| column | type |
|---|---|
| `sku` | `VARCHAR` |
| `uid` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `url` | `VARCHAR` |
| `category` | `VARCHAR` |
| `type` | `VARCHAR` |
| `varietal` | `VARCHAR` |
| `region` | `VARCHAR` |
| `country` | `VARCHAR` |
| `class` | `VARCHAR` |
| `size` | `VARCHAR` |
| `price` | `VARCHAR` |
| `msrp` | `VARCHAR` |
| `rating` | `VARCHAR` |
| `rating_count` | `VARCHAR` |
| `in_stock` | `VARCHAR` |
| `on_sale` | `VARCHAR` |
| `source_certified` | `VARCHAR` |
| `image` | `VARCHAR` |
| `raw_json` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `abc_facets.py:117` | `write_parquet` | flat (full overwrite) | no |
