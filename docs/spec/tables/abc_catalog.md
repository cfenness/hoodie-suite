# `abc_catalog`

|  |  |
|---|---|
| Status | landed |
| Rows | 14,098 |
| Columns | 7 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `abc-catalog`, `abc-fws` |
| URI | `s3://hoodie-suite-warehouse/warehouse/abc_catalog.parquet` |


## Columns

| column | type |
|---|---|
| `sku` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `size` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `price` | `DOUBLE` |
| `url` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `abc_catalog.py:77` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `abc_catalog.py:68` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `abc_fws_scraper.py:435` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
