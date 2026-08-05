# Binny's — `binnys`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `binnys` |
| Runs | `import binnys_scraper as m; m.pull(crawl_all=True)` |
| Module | `unifyd/binnys_scraper.py` — 325 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** Algolia feed


## 2. Transport

_No literal endpoint constant in `binnys_scraper.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `observe`, `raw_capture`, `warehouse`


## 3. What it lands


### `binnys_products`

1,534,862 rows · 30 columns


| column | type | filled |
|---|---|---|
| `sku` | `VARCHAR` | 100.0% |
| `store` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 93.6% |
| `varietal` | `VARCHAR` | 87.2% |
| `region` | `VARCHAR` | 24.6% |
| `origin` | `VARCHAR` | 97.3% |
| `category` | `VARCHAR` | 100.0% |
| `department` | `VARCHAR` | 100.0% |
| `item_size` | `VARCHAR` | 100.0% |
| `unit_label` | `VARCHAR` | 100.0% |
| `case_pack` | `DOUBLE` | 100.0% |
| `proof` | `DOUBLE` | 60.4% |
| `abv` | `DOUBLE` | 60.4% |
| `thc_mg` | `INTEGER` | **0%** ‹never populated› |
| `cbd_mg` | `INTEGER` | **0%** ‹never populated› |
| `rating` | `DOUBLE` | 6.1% |
| `reviews` | `DOUBLE` | 6.2% |
| `discount_pct` | `DOUBLE` | 100.0% |
| `deal_of_week` | `BOOLEAN` | 100.0% |
| `is_sold_out` | `BOOLEAN` | 100.0% |
| `in_store_only` | `BOOLEAN` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `short_desc` | `VARCHAR` | 100.0% |
| `product_url` | `VARCHAR` | 100.0% |
| `image` | `VARCHAR` | 100.0% |
| `price` | `DOUBLE` | 95.7% |
| `qty` | `BIGINT` | 100.0% |
| `raw_json` | `VARCHAR` | **0%** ‹never populated› |
| `__b` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

> **3 columns never populated:** `thc_mg`, `cbd_mg`, `raw_json`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `binnys_scraper.py:281` (write_full_rebuild), `binnys_scraper.py:283` (write_accumulate)


## 4. `binnys_scraper.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
binnys_scraper.py — STORE-LEVEL price + inventory tracker for Binny's (binnys.com).

Binny's runs on Algolia (public search key, client-exposed by design), and each product
record carries `storesPriceAndInventory` — a per-store array with a NUMERIC unit count
(`purchaseAvailability`) and per-store prices. So we get the real prize: store-level price
+ inventory, and the day-over-day delta of `purchaseAvailability` per (sku, store) ≈
directional UNITS SOLD. No scraping, no Bright Data — the same call the site's search makes.

connId: `binnys`. Snapshot is keyed by `sku|storeCode`; the run's headline delta is
`units_moved` (net depletion since the last pull).

Algolia app id / index / key are env-overridable (`BINNYS_ALGOLIA_*`; defaults discovered
on binnys.com). Self-reports `degraded` if the key rotates or the per-store schema changes.
```


## 5. Raw source fields

Endpoint: `POST {app}-dsn.algolia.net/1/indexes/Products_Production/query (public search key)` · grain: product × store


| raw field | meaning | maps to |
|---|---|---|
| `objectID` | product id (Binny's SKU) | `sku` |
| `storesPriceAndInventory[].purchaseAvailability` | EXACT per-store on-hand units | `qty` |
| `storesPriceAndInventory[].prices.{regularPrice,salePrice,isOnSale}` | per-store price | `price` |
| `productName / productBrandName` | name / brand | `name / brand` |
| `proof` | proof → ABV (proof/2) — the big gap this audit closed (was capturing NO ABV) | `proof / abv` |
| `productVarietal / region / country / productType / productDepartment` | geo + type + dept | `varietal / region / origin / category / department` |
| `itemSize / priceUnitLabel` | size + '750 ml Bottle' unit label | `item_size / unit_label` |
| `casePack` | bottles per case | `case_pack` |
| `ratingNumber / reviewsAmount` | ratings | `rating / reviews` |
| `pricePercentDiscount / isDealOfTheWeek` | promo/discount signals | `discount_pct / deal_of_week` |
| `isSoldOut / isInStoreOnly / inStockStores / onSaleStores` | availability signals | `is_sold_out / in_store_only` |
| `shortDescription / productDescriptionLong` | descriptions | `short_desc` |
| `productUrl / productLink` | product URL | `product_url` |
| `imageUrl / relativeImageUrl` | image | `image` |
| `thcMgPer{Serving,Unit,SellPack} / cbdMgPer*` | THC/CBD dose — schema-present but EMPTY at Binny's across all hemp products (captured anyway, future-proof — NOT a live THC-mg source) | `thc_mg / cbd_mg` |
| `designations / hierarchicalCategories / cigarShape\|Size\|Strength\|Wrapper` | wine designations / cat tree / cigar attrs | `raw_json` |
| `pointsMax/Min / variantCode / replacementCode / priceBoostIndex / date_timestamp` | loyalty/internal | `DROP:internal` |
| `(no upc/gtin)` | Binny's Algolia exposes NO UPC — matching rests on name-key + variantCode | `—` |


binnys_scraper.py to_snapshot. Live-audited: 14,678 cells all with qty; proof→ABV on ~43%. Also fixed: the scraper now write_accumulates binnys_products + observe (was snapshot-JSON only).
