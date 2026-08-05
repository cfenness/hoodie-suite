# `bevalc_chains`

|  |  |
|---|---|
| Status | landed |
| Rows | 127 |
| Columns | 14 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | flat (full overwrite) |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | — |
| URI | `s3://hoodie-suite-warehouse/warehouse/bevalc_chains.parquet` |


## Columns

| column | type |
|---|---|
| `chain` | `VARCHAR` |
| `premise` | `VARCHAR` |
| `channel` | `VARCHAR` |
| `banners` | `VARCHAR` |
| `est_locations` | `BIGINT` |
| `website` | `VARCHAR` |
| `pricing` | `VARCHAR` |
| `inventory` | `VARCHAR` |
| `method` | `VARCHAR` |
| `auth` | `VARCHAR` |
| `note` | `VARCHAR` |
| `is_source` | `BOOLEAN` |
| `yields` | `VARCHAR` |
| `family` | `VARCHAR` |


## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `chains.py:201` | `write_parquet` | flat (full overwrite) | no |
