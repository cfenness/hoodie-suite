# `agg_geo_stage`

|  |  |
|---|---|
| Status | landed |
| Rows | 409,882 |
| Columns | 29 |
| Storage | partitioned |
| Partitions | 12 |
| Schema drift | uniform in sample |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/agg_geo_stage/s05_b0001.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `chain` | `VARCHAR` | 7.6% |
| `is_chain` | `BOOLEAN` | 100.0% |
| `f_beer` | `BOOLEAN` | 100.0% |
| `f_wine` | `BOOLEAN` | 100.0% |
| `f_spirits` | `BOOLEAN` | 100.0% |
| `f_hemp` | `BOOLEAN` | 100.0% |
| `f_cannabis` | `BOOLEAN` | 100.0% |
| `f_rtd_spirits` | `BOOLEAN` | 100.0% |
| `flag_basis` | `VARCHAR` | 100.0% |
| `license_conflict` | `BOOLEAN` | 100.0% |
| `address` | `VARCHAR` | **4.2%** |
| `city` | `VARCHAR` | **4.1%** |
| `state` | `VARCHAR` | **4.1%** |
| `zip` | `VARCHAR` | **4.1%** |
| `lat` | `INTEGER` | **4.2%** |
| `lng` | `INTEGER` | **4.2%** |
| `phone` | `VARCHAR` | **0%** ‹never populated› |
| `addr_valid` | `BOOLEAN` | 100.0% |
| `hoodie_outlet` | `VARCHAR` | 100.0% |
| `name_key` | `VARCHAR` | 99.9% |
| `phone_norm` | `VARCHAR` | **0%** ‹never populated› |
| `addr_key` | `VARCHAR` | **0%** ‹never populated› |
| `geo_cell` | `VARCHAR` | **0%** ‹never populated› |
| `county_fips` | `VARCHAR` | **0%** ‹never populated› |
| `geo_precision` | `VARCHAR` | 100.0% |
| `staged_at` | `BIGINT` | 100.0% |

Fill measured over **newest 12 of 12 partitions** (409,882 rows).

> **5 columns never populated:** `phone`, `phone_norm`, `addr_key`, `geo_cell`, `county_fips`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `aggregator_geo.py:66` | `write_partition` | partitioned (append-only parts) | no |
