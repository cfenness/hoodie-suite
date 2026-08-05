# `label_reads`

|  |  |
|---|---|
| Status | landed |
| Rows | 4 |
| Columns | 39 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/label_reads.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `url` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `host` | `VARCHAR` | 100.0% |
| `method` | `VARCHAR` | 100.0% |
| `vision` | `BOOLEAN` | 100.0% |
| `raw_json` | `VARCHAR` | 100.0% |
| `provenance_json` | `VARCHAR` | 100.0% |
| `ts` | `BIGINT` | 100.0% |
| `brand` | `VARCHAR` | 50.0% |
| `product_name` | `VARCHAR` | 75.0% |
| `size` | `VARCHAR` | **0%** ‹never populated› |
| `size_options` | `VARCHAR` | **0%** ‹never populated› |
| `price` | `VARCHAR` | 50.0% |
| `category` | `VARCHAR` | **0%** ‹never populated› |
| `description` | `VARCHAR` | 50.0% |
| `image` | `VARCHAR` | 50.0% |
| `varietal` | `VARCHAR` | 25.0% |
| `wine_type` | `VARCHAR` | **0%** ‹never populated› |
| `style` | `VARCHAR` | **0%** ‹never populated› |
| `body` | `VARCHAR` | **0%** ‹never populated› |
| `abv` | `VARCHAR` | 50.0% |
| `proof` | `VARCHAR` | **0%** ‹never populated› |
| `vintage` | `VARCHAR` | **0%** ‹never populated› |
| `closure` | `VARCHAR` | 25.0% |
| `country` | `VARCHAR` | 50.0% |
| `state` | `VARCHAR` | 25.0% |
| `region` | `VARCHAR` | 25.0% |
| `sub_region` | `VARCHAR` | **0%** ‹never populated› |
| `appellation` | `VARCHAR` | **0%** ‹never populated› |
| `origin` | `VARCHAR` | 25.0% |
| `bottled_in` | `VARCHAR` | **0%** ‹never populated› |
| `upc` | `VARCHAR` | 50.0% |
| `finish` | `VARCHAR` | **0%** ‹never populated› |
| `taste` | `VARCHAR` | **0%** ‹never populated› |
| `food_pairing` | `VARCHAR` | **0%** ‹never populated› |
| `expert_rating` | `VARCHAR` | **0%** ‹never populated› |
| `customer_rating` | `VARCHAR` | **0%** ‹never populated› |
| `rating_count` | `VARCHAR` | **0%** ‹never populated› |
| `gov_warning` | `VARCHAR` | **0%** ‹never populated› |

Fill measured over **full table** (4 rows).

> **18 columns never populated:** `size`, `size_options`, `category`, `wine_type`, `style`, `body`, `proof`, `vintage`, `sub_region`, `appellation`, `bottled_in`, `finish`, `taste`, `food_pairing`, `expert_rating`, `customer_rating`, `rating_count`, `gov_warning`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `label_reader.py:449` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
