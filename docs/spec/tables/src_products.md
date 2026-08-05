# `src_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,001,832 |
| Columns | 22 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/src_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `source_id` | `VARCHAR` | 80.2% |
| `hoodie_product` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `product_name` | `VARCHAR` | 100.0% |
| `name_key` | `VARCHAR` | 100.0% |
| `flavor` | `VARCHAR` | 9.0% |
| `category` | `VARCHAR` | 97.3% |
| `product_type_id` | `BIGINT` | 100.0% |
| `product_type` | `VARCHAR` | 63.7% |
| `class_type` | `VARCHAR` | 54.3% |
| `abv` | `DOUBLE` | **3.4%** |
| `proof` | `DOUBLE` | **3.4%** |
| `varietal` | `VARCHAR` | 32.9% |
| `origin` | `VARCHAR` | 26.5% |
| `origin_class` | `VARCHAR` | 26.8% |
| `region` | `VARCHAR` | **1.4%** |
| `age_years` | `BIGINT` | **2.2%** |
| `volume_tier` | `VARCHAR` | 28.2% |
| `organic` | `BOOLEAN` | 100.0% |
| `non_alc` | `BOOLEAN` | 100.0% |
| `image` | `VARCHAR` | 64.5% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:226` | `write_parquet` | flat (full overwrite) | no |
