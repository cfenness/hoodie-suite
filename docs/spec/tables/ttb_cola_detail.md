# `ttb_cola_detail`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,858,375 |
| Columns | 21 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `ttb-enrich` |
| URI | `s3://hoodie-suite-warehouse/warehouse/ttb_cola_detail.parquet` |


## Columns

| column | type |
|---|---|
| `ttb_id` | `VARCHAR` |
| `status` | `VARCHAR` |
| `vendor_code` | `VARCHAR` |
| `serial_number` | `VARCHAR` |
| `class_type_code` | `VARCHAR` |
| `class_type_desc` | `VARCHAR` |
| `origin_code` | `VARCHAR` |
| `brand_name` | `VARCHAR` |
| `fanciful_name` | `VARCHAR` |
| `application_type` | `VARCHAR` |
| `for_sale_in` | `VARCHAR` |
| `net_contents` | `VARCHAR` |
| `wine_vintage` | `VARCHAR` |
| `grape_varietal` | `VARCHAR` |
| `alcohol_content` | `VARCHAR` |
| `formula` | `VARCHAR` |
| `approval_date` | `VARCHAR` |
| `qualifications` | `VARCHAR` |
| `plant_permit` | `VARCHAR` |
| `label_image_url` | `VARCHAR` |
| `other_json` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `ttb_pull.py:220` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
