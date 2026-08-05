# `specs_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,029 |
| Columns | 23 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated), flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `specs` |
| URI | `s3://hoodie-suite-warehouse/warehouse/specs_products.parquet` |


## Columns

| column | type |
|---|---|
| `sku` | `VARCHAR` |
| `slug` | `VARCHAR` |
| `url` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `type` | `VARCHAR` |
| `varietal` | `VARCHAR` |
| `abv` | `VARCHAR` |
| `origin` | `VARCHAR` |
| `region` | `VARCHAR` |
| `state` | `VARCHAR` |
| `vintage` | `VARCHAR` |
| `tasting_notes` | `VARCHAR` |
| `pairs_with` | `VARCHAR` |
| `description` | `VARCHAR` |
| `price` | `DOUBLE` |
| `upc` | `VARCHAR` |
| `image` | `VARCHAR` |
| `in_stock_stores` | `BIGINT` |
| `store_count` | `BIGINT` |
| `units_total` | `BIGINT` |
| `stores_tracked` | `BIGINT` |
| `raw_json` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `specs_scraper.py:419` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `specs_scraper.py:410` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `specs_scraper.py:415` | `write_parquet` | flat (full overwrite) | no |
