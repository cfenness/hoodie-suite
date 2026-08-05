# `wb_queue`

|  |  |
|---|---|
| Status | landed |
| Rows | 4,000 |
| Columns | 23 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/wb_queue.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `sku_key` | `VARCHAR` | 100.0% |
| `item_key` | `VARCHAR` | 100.0% |
| `product_key` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `product_name` | `VARCHAR` | 100.0% |
| `size_ml` | `BIGINT` | 82.0% |
| `container` | `VARCHAR` | 83.4% |
| `pack` | `BIGINT` | **1.1%** |
| `upc` | `VARCHAR` | **0.1%** |
| `sources` | `BIGINT` | 100.0% |
| `source_list` | `VARCHAR[]` | 100.0% |
| `source_rows` | `BIGINT` | 100.0% |
| `gtin` | `INTEGER` | **0%** ‹never populated› |
| `image` | `VARCHAR` | 39.0% |
| `varietal` | `VARCHAR` | 34.7% |
| `region` | `VARCHAR` | **1.2%** |
| `sub_region` | `INTEGER` | **0%** ‹never populated› |
| `appellation` | `INTEGER` | **0%** ‹never populated› |
| `origin` | `VARCHAR` | 69.5% |
| `bottled_in` | `INTEGER` | **0%** ‹never populated› |
| `abv` | `DOUBLE` | 84.1% |
| `category` | `VARCHAR` | 100.0% |
| `style` | `VARCHAR` | **1.3%** |

Fill measured over **full table** (4,000 rows).

> **4 columns never populated:** `gtin`, `sub_region`, `appellation`, `bottled_in`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `wb_views.py:313` | `write_parquet` | flat (full overwrite) | no |
