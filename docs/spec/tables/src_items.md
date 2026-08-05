# `src_items`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,059,584 |
| Columns | 11 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/src_items.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `source_id` | `VARCHAR` | 78.4% |
| `hoodie_item` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `product_name` | `VARCHAR` | 100.0% |
| `name_key` | `VARCHAR` | 100.0% |
| `product_type_id` | `BIGINT` | 100.0% |
| `product_type` | `VARCHAR` | 61.7% |
| `size_ml` | `BIGINT` | 32.5% |
| `container` | `VARCHAR` | **3.4%** |
| `volume_tier` | `VARCHAR` | 32.5% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `normalize.py:230` | `write_parquet` | flat (full overwrite) | no |
