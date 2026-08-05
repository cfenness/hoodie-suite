# `target_products`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,584 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `target` |
| URI | `s3://hoodie-suite-warehouse/warehouse/target_products.parquet` |


## Columns

| column | type |
|---|---|
| `tcin` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `price` | `DOUBLE` |
| `promo` | `INTEGER` |
| `image_url` | `VARCHAR` |
| `category` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `source` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `target_scraper.py:319` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `target_scraper.py:274` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
