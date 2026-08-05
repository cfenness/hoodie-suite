# Control states (OR/UT/NC/MT/ME/AL/BC/MontMD) — `control-states`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `control-states` |
| Runs | `import control_state as m; m.build_all()` |
| Module | `unifyd/control_state.py` — 331 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** per-state fetchers


## 2. Transport

| constant | value |
|---|---|
| `base` | `https://m7zjux4b6qin5verp-1.a1.typesense.net/collections/products/documents/search` |
| `base` | `https://revenuefiles.mt.gov/files/Alcoholic-Beverages/Agency-Liquor-Stores/Product-Information/Price-Disks/PriceDisk-%s-%d.xlsx` |
| `base` | `https://abs.utah.gov/wp-content/uploads/%s-%d-Product-List-FY%s-P%d.xlsx` |
| `base` | `https://www.mainespirits.com/sites/default/files/price_books/` |


**Depends on** `warehouse`


## 3. What it lands


### `or_pricing`

3,844 rows · 21 columns


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

### `ut_pricing`

10,239 rows · 14 columns


| column | type | filled |
|---|---|---|
| `CSC` | `VARCHAR` | 100.0% |
| `Description` | `VARCHAR` | 100.0% |
| `Div` | `VARCHAR` | 100.0% |
| `Dept` | `VARCHAR` | 100.0% |
| `Class` | `VARCHAR` | 100.0% |
| `Size` | `VARCHAR` | 100.0% |
| `Retail Price` | `VARCHAR` | 100.0% |
| `Item Status` | `VARCHAR` | 100.0% |
| `On Spa` | `VARCHAR` | 100.0% |
| `Vendor Name` | `VARCHAR` | 100.0% |
| `Vendor Cd` | `VARCHAR` | 100.0% |
| `Div Name` | `VARCHAR` | 100.0% |
| `Dept Name` | `VARCHAR` | 100.0% |
| `Class name` | `VARCHAR` | 100.0% |

Fill measured over **full table** (10,239 rows).

### `mont_sales`

319,028 rows · 9 columns


| column | type | filled |
|---|---|---|
| `calendar_year` | `VARCHAR` | 100.0% |
| `cal_month_num` | `VARCHAR` | 100.0% |
| `supplier` | `VARCHAR` | 99.9% |
| `item_code` | `VARCHAR` | 100.0% |
| `item_description` | `VARCHAR` | 100.0% |
| `item_type` | `VARCHAR` | 100.0% |
| `rtl_sales` | `VARCHAR` | 100.0% |
| `rtl_transfers` | `VARCHAR` | 100.0% |
| `whs_sales` | `VARCHAR` | 100.0% |

Fill measured over **full table** (319,028 rows).

## 4. `control_state.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
control_state.py — harvest control-state PRODUCT / PRICE / SALES data into the warehouse.

Control ("ABC") states run the stores themselves, so instead of outlet licenses they publish the
thing license states can't: product catalogs, official price books, and often transaction/period
SALES. That's a different, higher-value axis than the outlet map — it feeds the item master, a
pricing reference, and a real DEMAND signal (which also corroborates COLA tiering, [[cola-tiering]]).

This lands each source as its own warehouse dataset (Parquet on Tigris, queried by DuckDB —
[[warehouse-and-snowflake]]); some are 100k–300k+ rows, past the in-memory JSON state store. Sources
are Socrata open-data (datacenter-reachable, so they run on Fly). Discovery = the Socrata federated
catalog (api.us.socrata.com/api/catalog/v1); this CATALOG is the vetted subset worth taking now.

    python control_state.py --build            # land all (on a box with the Tigris creds, e.g. Fly)
    python control_state.py --build or_pricing # just one
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
