# `{}_stores`

|  |  |
|---|---|
| Status | **never landed** |
| Rows | — |
| Columns | — |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated), flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/{}_stores.parquet` |


> The table does not exist in the warehouse: `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/{}_stores.parquet' in region 'auto' (HTTP 404 Not Found)`


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `reconcile_ue_ids.py:36` | `write_parquet` | flat (full overwrite) | no |
| `run_ue_coverage_geo.py:55` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `ue_crawl.py:522` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
