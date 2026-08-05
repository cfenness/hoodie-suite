# `source_runs_log`

|  |  |
|---|---|
| Status | landed |
| Rows | 859 |
| Columns | 23 |
| Storage | partitioned |
| Partitions | 859 |
| Schema drift | **2 schemas in a 6-partition sample** |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/source_runs_log/1785943046927_80e9676b609148_688.parquet` |


## Columns

| column | type |
|---|---|
| `run_id` | `VARCHAR` |
| `source` | `VARCHAR` |
| `label` | `VARCHAR` |
| `klass` | `VARCHAR` |
| `ts_start` | `BIGINT` |
| `ts_end` | `BIGINT` |
| `duration_s` | `DOUBLE` |
| `status` | `VARCHAR` |
| `rows_before` | `BIGINT` |
| `rows_after` | `BIGINT` |
| `delta` | `BIGINT` |
| `tables` | `VARCHAR` |
| `error` | `VARCHAR` |
| `host` | `VARCHAR` |
| `cov_basis` | `VARCHAR` |
| `landed_items` | `BIGINT` |
| `expected_items` | `BIGINT` |
| `cov_items_pct` | `INTEGER` |
| `cov_items` | `VARCHAR` |
| `landed_stores` | `BIGINT` |
| `expected_stores` | `BIGINT` |
| `cov_stores_pct` | `DOUBLE` |
| `cov_stores` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `run_sources.py:555` | `write_partition` | partitioned (append-only parts) | no |
