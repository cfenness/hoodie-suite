# `master_quality_canon`

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
| Written by sources | `build-master-quality-canon` |
| URI | `s3://hoodie-suite-warehouse/warehouse/master_quality_canon.parquet` |


## Columns

| column | type |
|---|---|
| `version` | `BIGINT` |
| `ts` | `BIGINT` |
| `identity` | `VARCHAR` |
| `gold_version` | `BIGINT` |
| `n_pairs` | `BIGINT` |
| `n_all` | `BIGINT` |
| `tp` | `BIGINT` |
| `fp` | `BIGINT` |
| `fn` | `BIGINT` |
| `tn` | `BIGINT` |
| `precision` | `DOUBLE` |
| `recall` | `DOUBLE` |
| `f1` | `DOUBLE` |
| `coverage` | `DOUBLE` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `master_quality.py:224` | `write_accumulate` | accumulating (merge; bucketed if migrated) | no |
