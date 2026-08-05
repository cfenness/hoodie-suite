# `or_pricing`

|  |  |
|---|---|
| Status | landed |
| Rows | 3,844 |
| Columns | 21 |
| Storage | — |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `control-states` |
| URI | `s3://hoodie-suite-warehouse/warehouse/or_pricing.parquet` |


## Columns

| column | type | filled |
|---|---|---|
| `asofdate` | `VARCHAR` | 100.0% |
| `itemcode` | `VARCHAR` | 100.0% |
| `extendeditemcode` | `VARCHAR` | 100.0% |
| `description` | `VARCHAR` | 100.0% |
| `oregonproduct` | `BOOLEAN` | 100.0% |
| `itemstatus` | `VARCHAR` | 100.0% |
| `itemstatuscode` | `VARCHAR` | 100.0% |
| `category` | `VARCHAR` | 100.0% |
| `newitem` | `BOOLEAN` | 100.0% |
| `specialpricing` | `BOOLEAN` | 100.0% |
| `size` | `VARCHAR` | 100.0% |
| `proof` | `VARCHAR` | 100.0% |
| `priceperunit` | `VARCHAR` | 100.0% |
| `unitspercase` | `VARCHAR` | 100.0% |
| `pricepercase` | `VARCHAR` | 100.0% |
| `pricechange` | `VARCHAR` | 100.0% |
| `containertype` | `VARCHAR` | 99.9% |
| `containercount` | `VARCHAR` | 100.0% |
| `countryoforigin` | `VARCHAR` | 87.2% |
| `priceperoz` | `VARCHAR` | 100.0% |
| `age` | `VARCHAR` | 8.1% |

Fill measured over **full table** (3,844 rows).