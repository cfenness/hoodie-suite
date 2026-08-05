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

| column | type | filled |
|---|---|---|
| `sku_key` | `VARCHAR` | 100.0% |
| `item_key` | `VARCHAR` | 100.0% |
| `pack` | `BIGINT` | **1.7%** |
| `upc` | `VARCHAR` | 9.8% |
| `gtin` | `INTEGER` | **0%** ‹never populated› |
| `vintage` | `VARCHAR` | 20.4% |
| `edition` | `INTEGER` | **0%** ‹never populated› |
| `resolved_id` | `VARCHAR` | 100.0% |
| `source_rows` | `BIGINT` | 100.0% |
| `sources` | `BIGINT` | 100.0% |
| `source_list` | `VARCHAR[]` | 100.0% |
| `master_created_at` | `INTEGER` | 100.0% |
| `master_updated_at` | `INTEGER` | 100.0% |
| `updated_by` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

> **2 columns never populated:** `gtin`, `edition`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.
