# `price_coherence`

|  |  |
|---|---|
| Status | landed |
| Rows | 19,855 |
| Columns | 10 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/price_coherence.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `product_key` | `VARCHAR` | 100.0% |
| `n_priced` | `BIGINT` | 100.0% |
| `n_sources` | `BIGINT` | 100.0% |
| `median_unit_price` | `DOUBLE` | 100.0% |
| `min_unit_price` | `DOUBLE` | 100.0% |
| `max_unit_price` | `DOUBLE` | 100.0% |
| `spread_ratio` | `DOUBLE` | 100.0% |
| `agree_within_band` | `BIGINT` | 100.0% |
| `price_corroborated` | `BOOLEAN` | 100.0% |
| `divergent` | `BOOLEAN` | 100.0% |

Fill measured over **full table** (19,855 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:542` | `write_parquet` | flat (full overwrite) | no |
