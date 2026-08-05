# `label_reads`

|  |  |
|---|---|
| Status | landed |
| Rows | 4 |
| Columns | 39 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/label_reads.parquet` |


## Columns

| column | type |
|---|---|
| `url` | `VARCHAR` |
| `source` | `VARCHAR` |
| `host` | `VARCHAR` |
| `method` | `VARCHAR` |
| `vision` | `BOOLEAN` |
| `raw_json` | `VARCHAR` |
| `provenance_json` | `VARCHAR` |
| `ts` | `BIGINT` |
| `brand` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `size` | `VARCHAR` |
| `size_options` | `VARCHAR` |
| `price` | `VARCHAR` |
| `category` | `VARCHAR` |
| `description` | `VARCHAR` |
| `image` | `VARCHAR` |
| `varietal` | `VARCHAR` |
| `wine_type` | `VARCHAR` |
| `style` | `VARCHAR` |
| `body` | `VARCHAR` |
| `abv` | `VARCHAR` |
| `proof` | `VARCHAR` |
| `vintage` | `VARCHAR` |
| `closure` | `VARCHAR` |
| `country` | `VARCHAR` |
| `state` | `VARCHAR` |
| `region` | `VARCHAR` |
| `sub_region` | `VARCHAR` |
| `appellation` | `VARCHAR` |
| `origin` | `VARCHAR` |
| `bottled_in` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `finish` | `VARCHAR` |
| `taste` | `VARCHAR` |
| `food_pairing` | `VARCHAR` |
| `expert_rating` | `VARCHAR` |
| `customer_rating` | `VARCHAR` |
| `rating_count` | `VARCHAR` |
| `gov_warning` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `label_reader.py:449` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
