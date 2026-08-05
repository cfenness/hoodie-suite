# `market_projection`

|  |  |
|---|---|
| Status | landed |
| Rows | 5,921 |
| Columns | 11 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-representativeness` |
| URI | `s3://hoodie-suite-warehouse/warehouse/market_projection.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `state` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `universe_outlets` | `BIGINT` | 100.0% |
| `obs_stores` | `BIGINT` | 100.0% |
| `coverage` | `DOUBLE` | 100.0% |
| `observed_units` | `BIGINT` | 100.0% |
| `projected_units` | `INTEGER` | **0%** ‹never populated› |
| `ci_low` | `INTEGER` | **0%** ‹never populated› |
| `ci_high` | `INTEGER` | **0%** ‹never populated› |
| `ci_pct` | `INTEGER` | **0%** ‹never populated› |
| `projected_status` | `VARCHAR` | 100.0% |

Fill measured over **full table** (5,921 rows).

> **4 columns never populated:** `projected_units`, `ci_low`, `ci_high`, `ci_pct`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `representativeness.py:112` | `write_parquet` | flat (full overwrite) | no |
