# `src_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,001,832 |
| Columns | 22 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/src_products.parquet` |


## Columns

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `source_id` | `VARCHAR` |
| `hoodie_product` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `name_key` | `VARCHAR` |
| `flavor` | `VARCHAR` |
| `category` | `VARCHAR` |
| `product_type_id` | `BIGINT` |
| `product_type` | `VARCHAR` |
| `class_type` | `VARCHAR` |
| `abv` | `DOUBLE` |
| `proof` | `DOUBLE` |
| `varietal` | `VARCHAR` |
| `origin` | `VARCHAR` |
| `origin_class` | `VARCHAR` |
| `region` | `VARCHAR` |
| `age_years` | `BIGINT` |
| `volume_tier` | `VARCHAR` |
| `organic` | `BOOLEAN` |
| `non_alc` | `BOOLEAN` |
| `image` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:226` | `write_parquet` | flat (full overwrite) | no |
