# `vip_brandbuilder_sellsheet_packages`

|  |  |
|---|---|
| Status | landed |
| Rows | 7,609 |
| Columns | 7 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/vip_brandbuilder_sellsheet_packages.parquet` |


## Columns

| column | type |
|---|---|
| `sell_sheet_url` | `VARCHAR` |
| `package_name` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `distributor_ids` | `VARCHAR` |
| `product_ids` | `VARCHAR` |
| `n_source_rows` | `BIGINT` |
| `extracted_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `vip_brandbuilder_sellsheets.py:212` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
