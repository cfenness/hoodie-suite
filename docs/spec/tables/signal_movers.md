# `signal_movers`

|  |  |
|---|---|
| Status | landed |
| Rows | 1,015 |
| Columns | 12 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-velocity-signals` |
| URI | `s3://hoodie-suite-warehouse/warehouse/signal_movers.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `brand` | `VARCHAR` | 100.0% |
| `week` | `TIMESTAMP` | 100.0% |
| `prev_week` | `TIMESTAMP` | 100.0% |
| `matched_cells` | `BIGINT` | 100.0% |
| `units` | `DOUBLE` | 100.0% |
| `prev_units` | `DOUBLE` | 100.0% |
| `delta_units` | `DOUBLE` | 100.0% |
| `pct_change` | `DOUBLE` | 100.0% |
| `confidence` | `DOUBLE` | 100.0% |
| `partial` | `BOOLEAN` | **0%** ‹never populated› |
| `cur_obs_days` | `INTEGER` | 100.0% |
| `prev_obs_days` | `INTEGER` | 100.0% |

Fill measured over **full table** (1,015 rows).

> **1 column never populated:** `partial`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.
