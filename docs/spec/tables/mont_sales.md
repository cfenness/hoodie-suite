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

| column | type |
|---|---|
| `calendar_year` | `VARCHAR` |
| `cal_month_num` | `VARCHAR` |
| `supplier` | `VARCHAR` |
| `item_code` | `VARCHAR` |
| `item_description` | `VARCHAR` |
| `item_type` | `VARCHAR` |
| `rtl_sales` | `VARCHAR` |
| `rtl_transfers` | `VARCHAR` |
| `whs_sales` | `VARCHAR` |
