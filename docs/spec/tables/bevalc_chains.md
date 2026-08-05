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

| column | type | filled |
|---|---|---|
| `chain` | `VARCHAR` | 100.0% |
| `premise` | `VARCHAR` | 100.0% |
| `channel` | `VARCHAR` | 100.0% |
| `banners` | `VARCHAR` | 22.8% |
| `est_locations` | `BIGINT` | 100.0% |
| `website` | `VARCHAR` | 100.0% |
| `pricing` | `VARCHAR` | 100.0% |
| `inventory` | `VARCHAR` | 100.0% |
| `method` | `VARCHAR` | 100.0% |
| `auth` | `VARCHAR` | 100.0% |
| `note` | `VARCHAR` | 43.3% |
| `is_source` | `BOOLEAN` | 100.0% |
| `yields` | `VARCHAR` | 97.6% |
| `family` | `VARCHAR` | 100.0% |

Fill measured over **full table** (127 rows).

## Writers

| module:line | call | layout | pins dtypes |
|---|---|---|---|
| `chains.py:201` | `write_parquet` | flat (full overwrite) | no |
