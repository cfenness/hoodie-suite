# `specs_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,029 |
| Columns | 23 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated), flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `specs` |
| URI | `s3://hoodie-suite-warehouse/warehouse/specs_products.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `sku` | `VARCHAR` | 100.0% |
| `slug` | `VARCHAR` | 100.0% |
| `url` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 71.1% |
| `type` | `VARCHAR` | 92.4% |
| `varietal` | `VARCHAR` | **0.2%** |
| `abv` | `VARCHAR` | **1.7%** |
| `origin` | `VARCHAR` | 86.2% |
| `region` | `VARCHAR` | 28.9% |
| `state` | `VARCHAR` | **0.3%** |
| `vintage` | `VARCHAR` | **3.5%** |
| `tasting_notes` | `VARCHAR` | **1.7%** |
| `pairs_with` | `VARCHAR` | **0%** ‹never populated› |
| `description` | `VARCHAR` | 51.2% |
| `price` | `DOUBLE` | 100.0% |
| `upc` | `VARCHAR` | 72.2% |
| `image` | `VARCHAR` | 100.0% |
| `in_stock_stores` | `BIGINT` | 100.0% |
| `store_count` | `BIGINT` | 100.0% |
| `units_total` | `BIGINT` | 12.7% |
| `stores_tracked` | `BIGINT` | 12.7% |
| `raw_json` | `VARCHAR` | 100.0% |

Fill measured over **full table** (1,029 rows).

> **1 column never populated:** `pairs_with`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `specs_scraper.py:419` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `specs_scraper.py:410` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `specs_scraper.py:415` | `write_parquet` | flat (full overwrite) | no |
