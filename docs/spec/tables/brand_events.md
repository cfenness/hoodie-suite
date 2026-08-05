# `brand_events`

|  |  |
|---|---|
| Status | landed |
| Rows | 329 |
| Columns | 23 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `dam-bacardi` |
| URI | `s3://hoodie-suite-warehouse/warehouse/brand_events.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `event_id` | `VARCHAR` | 100.0% |
| `hoodie_brand_id` | `VARCHAR` | 100.0% |
| `brand_key` | `INTEGER` | **0%** ‹never populated› |
| `canon_brand` | `INTEGER` | **0%** ‹never populated› |
| `brand_resolution` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `sku_id` | `INTEGER` | **0%** ‹never populated› |
| `event_type` | `VARCHAR` | 100.0% |
| `event_date` | `VARCHAR` | 87.8% |
| `event_date_precision` | `VARCHAR` | 100.0% |
| `market` | `VARCHAR` | 27.7% |
| `price` | `DOUBLE` | **3.3%** |
| `currency` | `VARCHAR` | **3.3%** |
| `abv` | `DOUBLE` | **3.3%** |
| `title` | `VARCHAR` | 100.0% |
| `asset_count` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `source_id` | `VARCHAR` | 100.0% |
| `source_asset_ids` | `VARCHAR` | 100.0% |
| `source_url` | `VARCHAR` | 100.0% |
| `rights_ref` | `VARCHAR` | 100.0% |
| `field_provenance` | `VARCHAR` | 100.0% |
| `fetched_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (329 rows).

> **3 columns never populated:** `brand_key`, `canon_brand`, `sku_id`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dam.py:776` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
