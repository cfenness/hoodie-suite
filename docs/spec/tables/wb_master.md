# `wb_master`

|  |  |
|---|---|
| Status | landed |
| Rows | 4,000 |
| Columns | 23 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/wb_master.parquet` |


## Columns

| column | type |
|---|---|
| `sku_key` | `VARCHAR` |
| `item_key` | `VARCHAR` |
| `product_key` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `size_ml` | `BIGINT` |
| `container` | `VARCHAR` |
| `pack` | `BIGINT` |
| `upc` | `VARCHAR` |
| `sources` | `BIGINT` |
| `source_list` | `VARCHAR[]` |
| `source_rows` | `BIGINT` |
| `gtin` | `INTEGER` |
| `image` | `VARCHAR` |
| `varietal` | `VARCHAR` |
| `region` | `VARCHAR` |
| `sub_region` | `INTEGER` |
| `appellation` | `INTEGER` |
| `origin` | `VARCHAR` |
| `bottled_in` | `INTEGER` |
| `abv` | `DOUBLE` |
| `category` | `VARCHAR` |
| `style` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `wb_views.py:312` | `write_parquet` | flat (full overwrite) | no |
