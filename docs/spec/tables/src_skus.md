# `src_skus`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,084,333 |
| Columns | 15 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/src_skus.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `source_id` | `VARCHAR` | 78.0% |
| `upc` | `VARCHAR` | 24.5% |
| `upc_norm` | `VARCHAR` | 24.4% |
| `hoodie_sku` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `product_name` | `VARCHAR` | 100.0% |
| `name_key` | `VARCHAR` | 100.0% |
| `product_type_id` | `BIGINT` | 100.0% |
| `product_type` | `VARCHAR` | 58.1% |
| `size_ml` | `BIGINT` | 33.2% |
| `container` | `VARCHAR` | **3.7%** |
| `pack` | `BIGINT` | **4.3%** |
| `pack_size` | `BIGINT` | 100.0% |
| `pack_type` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:233` | `write_parquet` | flat (full overwrite) | no |
