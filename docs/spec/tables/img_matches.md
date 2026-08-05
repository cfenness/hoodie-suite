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

| column | type |
|---|---|
| `a_source` | `VARCHAR` |
| `a_sku` | `VARCHAR` |
| `a_upc` | `VARCHAR` |
| `b_source` | `VARCHAR` |
| `b_sku` | `VARCHAR` |
| `b_upc` | `VARCHAR` |
| `cosine` | `DOUBLE` |
| `name_sim` | `DOUBLE` |
| `class_agree` | `BIGINT` |
| `varietal_agree` | `BIGINT` |
| `flavor_agree` | `BIGINT` |
| `size_agree` | `BIGINT` |
| `confidence` | `DOUBLE` |
| `verdict` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `img_embed.py:210` | `write_parquet` | flat (full overwrite) | no |
