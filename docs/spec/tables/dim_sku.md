# `dim_sku`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,180,839 |
| Columns | 14 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-product-master` |
| URI | `s3://hoodie-suite-warehouse/warehouse/dim_sku.parquet` |


## Columns

| column | type |
|---|---|
| `sku_key` | `VARCHAR` |
| `item_key` | `VARCHAR` |
| `pack` | `BIGINT` |
| `upc` | `VARCHAR` |
| `gtin` | `INTEGER` |
| `vintage` | `VARCHAR` |
| `edition` | `INTEGER` |
| `resolved_id` | `VARCHAR` |
| `source_rows` | `BIGINT` |
| `sources` | `BIGINT` |
| `source_list` | `VARCHAR[]` |
| `master_created_at` | `INTEGER` |
| `master_updated_at` | `INTEGER` |
| `updated_by` | `VARCHAR` |
