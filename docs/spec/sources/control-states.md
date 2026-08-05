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


### `ut_pricing`

10,239 rows · 14 columns


| column | type |
|---|---|
| `CSC` | `VARCHAR` |
| `Description` | `VARCHAR` |
| `Div` | `VARCHAR` |
| `Dept` | `VARCHAR` |
| `Class` | `VARCHAR` |
| `Size` | `VARCHAR` |
| `Retail Price` | `VARCHAR` |
| `Item Status` | `VARCHAR` |
| `On Spa` | `VARCHAR` |
| `Vendor Name` | `VARCHAR` |
| `Vendor Cd` | `VARCHAR` |
| `Div Name` | `VARCHAR` |
| `Dept Name` | `VARCHAR` |
| `Class name` | `VARCHAR` |


### `mont_sales`

319,028 rows · 9 columns


| column | type |
|---|---|
| `calendar_year` | `VARCHAR` |
| `cal_month_num` | `VARCHAR` |
| `supplier` | `VARCHAR` |
| `item_code` | `VARCHAR` |
| `item_description` | `VARCHAR` |
| `item_type` | `VARCHAR` |
| `rtl_sales` | `VARCHAR` |
| `rtl_transfers` | `VARCHAR` |
| `whs_sales` | `VARCHAR` |


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
