# `_stage_product`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,609,448 |
| Columns | 39 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/_stage_product.parquet` |


## Columns

| column | type |
|---|---|
| `brand` | `VARCHAR` |
| `brand_group` | `INTEGER` |
| `product_name` | `VARCHAR` |
| `class_type` | `VARCHAR` |
| `core_name` | `VARCHAR` |
| `flavor` | `VARCHAR` |
| `abv` | `DOUBLE` |
| `style` | `VARCHAR` |
| `category` | `VARCHAR` |
| `origin` | `VARCHAR` |
| `country` | `VARCHAR` |
| `state` | `VARCHAR` |
| `bottled_in` | `INTEGER` |
| `region` | `VARCHAR` |
| `sub_region` | `INTEGER` |
| `appellation` | `INTEGER` |
| `varietal` | `VARCHAR` |
| `image` | `VARCHAR` |
| `taste` | `VARCHAR` |
| `body` | `INTEGER` |
| `food_pairing` | `VARCHAR` |
| `expert_rating` | `INTEGER` |
| `finish` | `INTEGER` |
| `size_ml` | `BIGINT` |
| `packsize` | `INTEGER` |
| `container` | `VARCHAR` |
| `pack` | `BIGINT` |
| `upc` | `VARCHAR` |
| `gtin` | `INTEGER` |
| `vintage` | `VARCHAR` |
| `edition` | `INTEGER` |
| `supplier` | `INTEGER` |
| `gtin14` | `VARCHAR` |
| `gs1_digital_link` | `VARCHAR` |
| `code_type` | `INTEGER` |
| `gs1_link_source` | `VARCHAR` |
| `price` | `DOUBLE` |
| `_source` | `VARCHAR` |
| `_source_id` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:813` | `write_parquet` | flat (full overwrite) | no |
