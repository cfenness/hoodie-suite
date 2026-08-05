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

| column | type | filled |
|---|---|---|
| `ttb_id` | `VARCHAR` | 100.0% |
| `image_file` | `VARCHAR` | 99.7% |
| `upc` | `VARCHAR` | 34.9% |
| `abv` | `VARCHAR` | 24.2% |
| `net_contents` | `VARCHAR` | 55.1% |
| `claims` | `VARCHAR` | 19.6% |
| `gov_warning` | `VARCHAR` | 98.7% |
| `ocr_chars` | `VARCHAR` | 99.0% |
| `front_label_url` | `VARCHAR` | 99.7% |
| `back_label_url` | `VARCHAR` | 40.9% |
| `label_urls` | `VARCHAR` | 99.7% |

Fill measured over **full table** (23,874 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `ttb_pull.py:222` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
