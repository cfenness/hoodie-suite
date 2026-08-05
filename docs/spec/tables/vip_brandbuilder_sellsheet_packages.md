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

| column | type | filled |
|---|---|---|
| `sell_sheet_url` | `VARCHAR` | 100.0% |
| `package_name` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 61.8% |
| `distributor_ids` | `VARCHAR` | 100.0% |
| `product_ids` | `VARCHAR` | 100.0% |
| `n_source_rows` | `BIGINT` | 100.0% |
| `extracted_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (7,609 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `vip_brandbuilder_sellsheets.py:212` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
