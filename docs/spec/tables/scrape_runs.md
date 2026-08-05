# `scrape_runs`

|  |  |
|---|---|
| Status | landed |
| Rows | 260 |
| Columns | 10 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/scrape_runs.parquet` |


## Columns

| column | type |
|---|---|
| `run_id` | `VARCHAR` |
| `connId` | `VARCHAR` |
| `status` | `VARCHAR` |
| `host` | `VARCHAR` |
| `startedAt` | `BIGINT` |
| `updatedAt` | `BIGINT` |
| `finishedAt` | `INTEGER` |
| `n` | `BIGINT` |
| `total` | `INTEGER` |
| `note` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `runlog.py:63` | `write_partition` | partitioned (append-only parts) | no |
