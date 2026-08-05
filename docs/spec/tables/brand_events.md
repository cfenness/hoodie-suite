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

| column | type |
|---|---|
| `event_id` | `VARCHAR` |
| `hoodie_brand_id` | `VARCHAR` |
| `brand_key` | `INTEGER` |
| `canon_brand` | `INTEGER` |
| `brand_resolution` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `sku_id` | `INTEGER` |
| `event_type` | `VARCHAR` |
| `event_date` | `VARCHAR` |
| `event_date_precision` | `VARCHAR` |
| `market` | `VARCHAR` |
| `price` | `DOUBLE` |
| `currency` | `VARCHAR` |
| `abv` | `DOUBLE` |
| `title` | `VARCHAR` |
| `asset_count` | `BIGINT` |
| `source` | `VARCHAR` |
| `source_id` | `VARCHAR` |
| `source_asset_ids` | `VARCHAR` |
| `source_url` | `VARCHAR` |
| `rights_ref` | `VARCHAR` |
| `field_provenance` | `VARCHAR` |
| `fetched_at` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `dam.py:776` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
