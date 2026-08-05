# `master_quality`

|  |  |
|---|---|
| Status | landed |
| Rows | 3 |
| Columns | 14 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | accumulating (merge; bucketed if migrated) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `build-master-quality` |
| URI | `s3://hoodie-suite-warehouse/warehouse/master_quality.parquet` |


## Columns

| column | type |
|---|---|
| `version` | `BIGINT` |
| `ts` | `BIGINT` |
| `n_pairs` | `BIGINT` |
| `n_pos` | `BIGINT` |
| `n_neg` | `BIGINT` |
| `n_resolvable` | `BIGINT` |
| `tp` | `BIGINT` |
| `fp` | `BIGINT` |
| `fn` | `BIGINT` |
| `tn` | `BIGINT` |
| `precision` | `DOUBLE` |
| `recall` | `DOUBLE` |
| `f1` | `DOUBLE` |
| `regressions` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `master_quality.py:150` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
