# `master_quality`

|  |  |
|---|---|
| Status | landed |
| Rows | 3 |
| Columns | 14 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-master-quality` |
| URI | `s3://hoodie-suite-warehouse/warehouse/master_quality.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `version` | `BIGINT` | 100.0% |
| `ts` | `BIGINT` | 100.0% |
| `n_pairs` | `BIGINT` | 100.0% |
| `n_pos` | `BIGINT` | 100.0% |
| `n_neg` | `BIGINT` | 100.0% |
| `n_resolvable` | `BIGINT` | 100.0% |
| `tp` | `BIGINT` | 100.0% |
| `fp` | `BIGINT` | 100.0% |
| `fn` | `BIGINT` | 100.0% |
| `tn` | `BIGINT` | 100.0% |
| `precision` | `DOUBLE` | 100.0% |
| `recall` | `DOUBLE` | 100.0% |
| `f1` | `DOUBLE` | 100.0% |
| `regressions` | `VARCHAR` | 33.3% |

Fill measured over **full table** (3 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `master_quality.py:150` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
