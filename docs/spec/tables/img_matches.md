# `img_matches`

|  |  |
|---|---|
| Status | landed |
| Rows | 8,737 |
| Columns | 14 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/img_matches.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `a_source` | `VARCHAR` | 100.0% |
| `a_sku` | `VARCHAR` | 99.3% |
| `a_upc` | `VARCHAR` | 21.3% |
| `b_source` | `VARCHAR` | 100.0% |
| `b_sku` | `VARCHAR` | 100.0% |
| `b_upc` | `VARCHAR` | 6.4% |
| `cosine` | `DOUBLE` | 100.0% |
| `name_sim` | `DOUBLE` | 100.0% |
| `class_agree` | `BIGINT` | 100.0% |
| `varietal_agree` | `BIGINT` | 100.0% |
| `flavor_agree` | `BIGINT` | 100.0% |
| `size_agree` | `BIGINT` | 100.0% |
| `confidence` | `DOUBLE` | 100.0% |
| `verdict` | `VARCHAR` | 100.0% |

Fill measured over **full table** (8,737 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `img_embed.py:210` | `write_parquet` | flat (full overwrite) | no |
