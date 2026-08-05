# `abc_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 9,399 |
| Columns | 21 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `abc-facets` |
| URI | `s3://hoodie-suite-warehouse/warehouse/abc_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `sku` | `VARCHAR` | 100.0% |
| `uid` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 99.9% |
| `url` | `VARCHAR` | 100.0% |
| `category` | `VARCHAR` | 100.0% |
| `type` | `VARCHAR` | 97.4% |
| `varietal` | `VARCHAR` | 22.2% |
| `region` | `VARCHAR` | **0%** ‹never populated› |
| `country` | `VARCHAR` | **0%** ‹never populated› |
| `class` | `VARCHAR` | 100.0% |
| `size` | `VARCHAR` | 82.8% |
| `price` | `VARCHAR` | 85.3% |
| `msrp` | `VARCHAR` | 85.3% |
| `rating` | `VARCHAR` | 40.2% |
| `rating_count` | `VARCHAR` | 40.2% |
| `in_stock` | `VARCHAR` | 100.0% |
| `on_sale` | `VARCHAR` | 33.3% |
| `source_certified` | `VARCHAR` | 100.0% |
| `image` | `VARCHAR` | 81.1% |
| `raw_json` | `VARCHAR` | 100.0% |

Fill measured over **full table** (9,399 rows).

> **2 columns never populated:** `region`, `country`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `abc_facets.py:117` | `write_parquet` | flat (full overwrite) | no |
