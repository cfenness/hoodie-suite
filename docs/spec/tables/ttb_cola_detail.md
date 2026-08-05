# `ttb_cola_detail`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,858,375 |
| Columns | 21 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `ttb-enrich` |
| URI | `s3://hoodie-suite-warehouse/warehouse/ttb_cola_detail.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `ttb_id` | `VARCHAR` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `vendor_code` | `VARCHAR` | 87.1% |
| `serial_number` | `VARCHAR` | 100.0% |
| `class_type_code` | `VARCHAR` | **0%** ‹never populated› |
| `class_type_desc` | `VARCHAR` | 100.0% |
| `origin_code` | `VARCHAR` | 100.0% |
| `brand_name` | `VARCHAR` | 100.0% |
| `fanciful_name` | `VARCHAR` | 33.6% |
| `application_type` | `VARCHAR` | 95.4% |
| `for_sale_in` | `VARCHAR` | **0.2%** |
| `net_contents` | `VARCHAR` | 28.4% |
| `wine_vintage` | `VARCHAR` | 69.2% |
| `grape_varietal` | `VARCHAR` | 85.3% |
| `alcohol_content` | `VARCHAR` | **0%** ‹never populated› |
| `formula` | `VARCHAR` | **2.7%** |
| `approval_date` | `VARCHAR` | 98.9% |
| `qualifications` | `VARCHAR` | **0%** ‹never populated› |
| `plant_permit` | `VARCHAR` | **0%** ‹never populated› |
| `label_image_url` | `VARCHAR` | 98.4% |
| `other_json` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

> **4 columns never populated:** `class_type_code`, `alcohol_content`, `qualifications`, `plant_permit`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `ttb_pull.py:220` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
