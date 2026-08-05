# `<dynamic>`

|  |  |
|---|---|
| Status | **never landed** |
| Rows | — |
| Columns | — |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated), flat (from csv), flat (full overwrite), partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/<dynamic>.parquet` |


> The table does not exist in the warehouse: `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/<dynamic>.parquet' in region 'auto' (HTTP 404 Not Found)`


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `bottle_dims.py:389` | `write_parquet` | flat (full overwrite) | no |
| `bottle_dims.py:387` | `write_parquet` | flat (full overwrite) | no |
| `census.py:123` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `cola_to_warehouse.py:40` | `write_parquet_from_csv` | flat (from csv) | no |
| `control_state.py:90` | `write_parquet` | flat (full overwrite) | no |
| `control_state.py:113` | `write_parquet` | flat (full overwrite) | no |
| `control_state.py:301` | `write_parquet` | flat (full overwrite) | no |
| `coverage.py:216` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `coverage.py:227` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `coverage.py:238` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `doordash.py:325` | `write_parquet` | flat (full overwrite) | no |
| `doordash.py:330` | `write_parquet` | flat (full overwrite) | no |
| `doordash_full.py:297` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `doordash_full.py:310` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `doordash_full.py:325` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `doordash_full.py:333` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `doordash_full.py:266` | `write_partition` | partitioned (append-only parts) | yes |
| `doordash_full.py:273` | `write_partition` | partitioned (append-only parts) | yes |
| `fold.py:225` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `hoodie_ids.py:203` | `write_parquet` | flat (full overwrite) | no |
| `iowa_bq.py:104` | `write_parquet` | flat (full overwrite) | no |
| `menu_site.py:400` | `write_parquet` | flat (full overwrite) | no |
| `normalize.py:718` | `write_parquet` | flat (full overwrite) | no |
| `off_premise.py:966` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `off_premise.py:1022` | `write_parquet` | flat (full overwrite) | no |
| `places.py:168` | `write_parquet` | flat (full overwrite) | no |
| `places.py:203` | `write_parquet` | flat (full overwrite) | no |
| `platform_census.py:186` | `write_parquet` | flat (full overwrite) | no |
| `platform_census.py:184` | `write_parquet` | flat (full overwrite) | no |
| `seed.py:137` | `write_parquet` | flat (full overwrite) | no |
| `server.py:3589` | `write_parquet` | flat (full overwrite) | no |
| `server.py:677` | `write_parquet` | flat (full overwrite) | no |
| `snapshot_land.py:51` | `write_parquet` | flat (full overwrite) | no |
| `snapshot_land.py:70` | `write_parquet` | flat (full overwrite) | no |
| `ue_catalog.py:478` | `write_partition` | partitioned (append-only parts) | yes |
| `ue_catalog.py:588` | `write_parquet` | flat (full overwrite) | no |
| `ue_catalog.py:590` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `ue_crawl.py:264` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `ue_feed_sweep.py:143` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `warehouse.py:301` | `write_parquet` | flat (full overwrite) | no |
| `warehouse.py:298` | `write_parquet` | flat (full overwrite) | no |
| `warehouse.py:384` | `write_parquet` | flat (full overwrite) | no |
| `xsource_queue.py:280` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
