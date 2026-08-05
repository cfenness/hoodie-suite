# `source_runs_log`

|  |  |
|---|---|
| Status | landed |
| Rows | 857 |
| Columns | 23 |
| Storage | partitioned |
| Partitions | 857 |
| Schema drift | **2 schemas in a 6-partition sample** |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/source_runs_log/1785925532741_1854540b1edd28_689.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `run_id` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `label` | `VARCHAR` | 100.0% |
| `klass` | `VARCHAR` | 100.0% |
| `ts_start` | `BIGINT` | 100.0% |
| `ts_end` | `BIGINT` | 100.0% |
| `duration_s` | `DOUBLE` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `rows_before` | `BIGINT` | 100.0% |
| `rows_after` | `BIGINT` | 100.0% |
| `delta` | `BIGINT` | 100.0% |
| `tables` | `VARCHAR` | 100.0% |
| `error` | `VARCHAR` | 37.5% |
| `host` | `VARCHAR` | 100.0% |
| `cov_basis` | `VARCHAR` | 100.0% |
| `landed_items` | `BIGINT` | 100.0% |
| `expected_items` | `BIGINT` | 100.0% |
| `cov_items_pct` | `DOUBLE` | 42.5% |
| `cov_items` | `VARCHAR` | 100.0% |
| `landed_stores` | `INTEGER` | 97.5% |
| `expected_stores` | `INTEGER` | 97.5% |
| `cov_stores_pct` | `INTEGER` | 45.0% |
| `cov_stores` | `VARCHAR` | 100.0% |

Fill measured over **newest 40 of 857 partitions** (40 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `run_sources.py:555` | `write_partition` | partitioned (append-only parts) | no |
