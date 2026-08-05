# `binnys_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,534,862 |
| Columns | 30 |
| Storage | bucketed |
| Partitions | 16 |
| Schema drift | uniform in sample |
| Write mode | accumulating (merge; bucketed if migrated), flat (full rebuild, layout-preserving) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `binnys` |
| URI | `manifest: _manifest/binnys_products.json` |


## Columns

| column | type |
|---|---|
| `sku` | `VARCHAR` |
| `store` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `varietal` | `VARCHAR` |
| `region` | `VARCHAR` |
| `origin` | `VARCHAR` |
| `category` | `VARCHAR` |
| `department` | `VARCHAR` |
| `item_size` | `VARCHAR` |
| `unit_label` | `VARCHAR` |
| `case_pack` | `DOUBLE` |
| `proof` | `DOUBLE` |
| `abv` | `DOUBLE` |
| `thc_mg` | `INTEGER` |
| `cbd_mg` | `INTEGER` |
| `rating` | `DOUBLE` |
| `reviews` | `DOUBLE` |
| `discount_pct` | `DOUBLE` |
| `deal_of_week` | `BOOLEAN` |
| `is_sold_out` | `BOOLEAN` |
| `in_store_only` | `BOOLEAN` |
| `is_hemp` | `BOOLEAN` |
| `short_desc` | `VARCHAR` |
| `product_url` | `VARCHAR` |
| `image` | `VARCHAR` |
| `price` | `DOUBLE` |
| `qty` | `BIGINT` |
| `raw_json` | `VARCHAR` |
| `__b` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `binnys_scraper.py:281` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
| `binnys_scraper.py:283` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
