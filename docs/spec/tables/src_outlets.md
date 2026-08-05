# `src_outlets`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,916,357 |
| Columns | 29 |
| Storage | bucketed |
| Partitions | 16 |
| Schema drift | uniform in sample |
| Write mode | accumulating (merge; bucketed if migrated), flat (full rebuild, layout-preserving) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `geocode`, `aggregator-geo`, `fast-geo`, `geo`, `ubereats-sitemap`, `postmates-sitemap`, `build-outlets` |
| URI | `manifest: _manifest/src_outlets.json` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `chain` | `VARCHAR` | 8.6% |
| `is_chain` | `BOOLEAN` | 100.0% |
| `f_beer` | `BOOLEAN` | 100.0% |
| `f_wine` | `BOOLEAN` | 100.0% |
| `f_spirits` | `BOOLEAN` | 100.0% |
| `f_hemp` | `BOOLEAN` | 100.0% |
| `f_cannabis` | `BOOLEAN` | 100.0% |
| `f_rtd_spirits` | `BOOLEAN` | 100.0% |
| `flag_basis` | `VARCHAR` | 100.0% |
| `license_conflict` | `BOOLEAN` | 100.0% |
| `address` | `VARCHAR` | 29.0% |
| `city` | `VARCHAR` | 59.3% |
| `state` | `VARCHAR` | 57.0% |
| `zip` | `VARCHAR` | 27.6% |
| `lat` | `DOUBLE` | 55.8% |
| `lng` | `DOUBLE` | 55.8% |
| `phone` | `VARCHAR` | **0.1%** |
| `addr_valid` | `BOOLEAN` | 100.0% |
| `hoodie_outlet` | `VARCHAR` | 100.0% |
| `name_key` | `VARCHAR` | 99.9% |
| `phone_norm` | `VARCHAR` | **0.1%** |
| `addr_key` | `VARCHAR` | 24.8% |
| `geo_cell` | `VARCHAR` | 25.0% |
| `county_fips` | `VARCHAR` | **4.5%** |
| `geo_precision` | `VARCHAR` | 100.0% |
| `__b` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `aggregator_geo.py:93` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `city_centroid.py:268` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `geocode.py:134` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `mappability.py:163` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `normalize.py:651` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
| `reconcile_ue_ids.py:45` | `write_full_rebuild` | flat (full rebuild, layout-preserving) | no |
| `refresh_fast.py:60` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
| `ue_sitemap.py:97` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
