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

| column | type |
|---|---|
| `source` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `chain` | `VARCHAR` |
| `is_chain` | `BOOLEAN` |
| `f_beer` | `BOOLEAN` |
| `f_wine` | `BOOLEAN` |
| `f_spirits` | `BOOLEAN` |
| `f_hemp` | `BOOLEAN` |
| `f_cannabis` | `BOOLEAN` |
| `f_rtd_spirits` | `BOOLEAN` |
| `flag_basis` | `VARCHAR` |
| `license_conflict` | `BOOLEAN` |
| `address` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `zip` | `VARCHAR` |
| `lat` | `DOUBLE` |
| `lng` | `DOUBLE` |
| `phone` | `VARCHAR` |
| `addr_valid` | `BOOLEAN` |
| `hoodie_outlet` | `VARCHAR` |
| `name_key` | `VARCHAR` |
| `phone_norm` | `VARCHAR` |
| `addr_key` | `VARCHAR` |
| `geo_cell` | `VARCHAR` |
| `county_fips` | `VARCHAR` |
| `geo_precision` | `VARCHAR` |
| `__b` | `VARCHAR` |


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
