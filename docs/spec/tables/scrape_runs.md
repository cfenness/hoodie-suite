# `scrape_runs`

|  |  |
|---|---|
| Status | landed |
| Rows | 260 |
| Columns | 10 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | partitioned (append-only parts) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/scrape_runs.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `run_id` | `VARCHAR` | 100.0% |
| `connId` | `VARCHAR` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `host` | `VARCHAR` | 100.0% |
| `startedAt` | `BIGINT` | 100.0% |
| `updatedAt` | `BIGINT` | 100.0% |
| `finishedAt` | `INTEGER` | **0%** ‹never populated› |
| `n` | `BIGINT` | 100.0% |
| `total` | `INTEGER` | **0%** ‹never populated› |
| `note` | `VARCHAR` | **0%** ‹never populated› |

Fill measured over **full table** (2 rows).

> **3 columns never populated:** `finishedAt`, `total`, `note`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `runlog.py:63` | `write_partition` | partitioned (append-only parts) | no |
