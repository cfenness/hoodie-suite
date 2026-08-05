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

| column | type |
|---|---|
| `product_key` | `VARCHAR` |
| `n_priced` | `BIGINT` |
| `n_sources` | `BIGINT` |
| `median_unit_price` | `DOUBLE` |
| `min_unit_price` | `DOUBLE` |
| `max_unit_price` | `DOUBLE` |
| `spread_ratio` | `DOUBLE` |
| `agree_within_band` | `BIGINT` |
| `price_corroborated` | `BOOLEAN` |
| `divergent` | `BOOLEAN` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `build_product_master.py:525` | `write_parquet` | flat (full overwrite) | no |
