# `or_pricing`

|  |  |
|---|---|
| Status | landed |
| Rows | 3,844 |
| Columns | 21 |
| Storage | single file |
| Partitions | — |
| Schema drift | — |
| Write mode | — |
| Declared in `table_spec.py` | no — schema is whatever the writer emits |
| Written by sources | `control-states` |
| URI | `s3://hoodie-suite-warehouse/warehouse/or_pricing.parquet` |


## Columns

| column | type |
|---|---|
| `asofdate` | `VARCHAR` |
| `itemcode` | `VARCHAR` |
| `extendeditemcode` | `VARCHAR` |
| `description` | `VARCHAR` |
| `oregonproduct` | `BOOLEAN` |
| `itemstatus` | `VARCHAR` |
| `itemstatuscode` | `VARCHAR` |
| `category` | `VARCHAR` |
| `newitem` | `BOOLEAN` |
| `specialpricing` | `BOOLEAN` |
| `size` | `VARCHAR` |
| `proof` | `VARCHAR` |
| `priceperunit` | `VARCHAR` |
| `unitspercase` | `VARCHAR` |
| `pricepercase` | `VARCHAR` |
| `pricechange` | `VARCHAR` |
| `containertype` | `VARCHAR` |
| `containercount` | `VARCHAR` |
| `countryoforigin` | `VARCHAR` |
| `priceperoz` | `VARCHAR` |
| `age` | `VARCHAR` |
