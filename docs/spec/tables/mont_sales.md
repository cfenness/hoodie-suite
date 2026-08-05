# `mont_sales`

|  |  |
|---|---|
| Status | landed |
| Rows | 319,028 |
| Columns | 9 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `control-states` |
| URI | `s3://hoodie-suite-warehouse/warehouse/mont_sales.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `calendar_year` | `VARCHAR` | 100.0% |
| `cal_month_num` | `VARCHAR` | 100.0% |
| `supplier` | `VARCHAR` | 99.9% |
| `item_code` | `VARCHAR` | 100.0% |
| `item_description` | `VARCHAR` | 100.0% |
| `item_type` | `VARCHAR` | 100.0% |
| `rtl_sales` | `VARCHAR` | 100.0% |
| `rtl_transfers` | `VARCHAR` | 100.0% |
| `whs_sales` | `VARCHAR` | 100.0% |

Fill measured over **full table** (319,028 rows).