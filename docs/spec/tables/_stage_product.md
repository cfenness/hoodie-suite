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

| column | type | filled |
|---|---|---|
| `brand` | `VARCHAR` | 100.0% |
| `brand_group` | `INTEGER` | **0%** ‹never populated› |
| `product_name` | `VARCHAR` | 100.0% |
| `class_type` | `VARCHAR` | 55.6% |
| `core_name` | `VARCHAR` | 93.9% |
| `flavor` | `VARCHAR` | 11.1% |
| `abv` | `DOUBLE` | 5.4% |
| `style` | `VARCHAR` | **2.3%** |
| `category` | `VARCHAR` | 96.1% |
| `origin` | `VARCHAR` | 10.1% |
| `country` | `VARCHAR` | **0.2%** |
| `state` | `VARCHAR` | **0%** ‹never populated› |
| `bottled_in` | `INTEGER` | **0%** ‹never populated› |
| `region` | `VARCHAR` | **3.8%** |
| `sub_region` | `INTEGER` | **0%** ‹never populated› |
| `appellation` | `INTEGER` | **0%** ‹never populated› |
| `varietal` | `VARCHAR` | 24.1% |
| `image` | `VARCHAR` | 81.6% |
| `taste` | `VARCHAR` | **0%** ‹never populated› |
| `body` | `INTEGER` | **0%** ‹never populated› |
| `food_pairing` | `VARCHAR` | **0.1%** |
| `expert_rating` | `INTEGER` | **0%** ‹never populated› |
| `finish` | `INTEGER` | **0%** ‹never populated› |
| `size_ml` | `BIGINT` | 38.0% |
| `packsize` | `INTEGER` | **0%** ‹never populated› |
| `container` | `VARCHAR` | **4.0%** |
| `pack` | `BIGINT` | **4.9%** |
| `upc` | `VARCHAR` | 26.2% |
| `gtin` | `INTEGER` | **0%** ‹never populated› |
| `vintage` | `VARCHAR` | **0.1%** |
| `edition` | `INTEGER` | **0%** ‹never populated› |
| `supplier` | `INTEGER` | **0%** ‹never populated› |
| `gtin14` | `VARCHAR` | **2.4%** |
| `gs1_digital_link` | `VARCHAR` | **2.4%** |
| `code_type` | `INTEGER` | **0%** ‹never populated› |
| `gs1_link_source` | `VARCHAR` | **2.4%** |
| `price` | `DOUBLE` | 15.2% |
| `_source` | `VARCHAR` | 100.0% |
| `_source_id` | `VARCHAR` | 73.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

> **14 columns never populated:** `brand_group`, `state`, `bottled_in`, `sub_region`, `appellation`, `taste`, `body`, `expert_rating`, `finish`, `packsize`, `gtin`, `edition`, `supplier`, `code_type`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:867` | `write_parquet` | flat (full overwrite) | no |
