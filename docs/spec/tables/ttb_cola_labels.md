# `ttb_cola_labels`

|  |  |
|---|---|
| Status | landed |
| Rows | 23,874 |
| Columns | 11 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `ttb-enrich` |
| URI | `s3://hoodie-suite-warehouse/warehouse/ttb_cola_labels.parquet` |


## Columns

| column | type |
|---|---|
| `ttb_id` | `VARCHAR` |
| `image_file` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `abv` | `VARCHAR` |
| `net_contents` | `VARCHAR` |
| `claims` | `VARCHAR` |
| `gov_warning` | `VARCHAR` |
| `ocr_chars` | `VARCHAR` |
| `front_label_url` | `VARCHAR` |
| `back_label_url` | `VARCHAR` |
| `label_urls` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `ttb_pull.py:222` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
