# `source_runs_log`

|  |  |
|---|---|
| Status | landed |
| Rows | 848 |
| Columns | 23 |
| Storage | partitioned |
| Partitions | 848 |
| Schema drift | **2 schemas in a 6-partition sample** |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/source_runs_log/1785925532741_1854540b1edd28_689.parquet` |


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
| `cov_items_pct` | `DOUBLE` |
| `cov_items` | `VARCHAR` |
| `landed_stores` | `INTEGER` |
| `expected_stores` | `INTEGER` |
| `cov_stores_pct` | `INTEGER` |
| `cov_stores` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `run_sources.py:555` | `write_partition` | partitioned (append-only parts) | no |
