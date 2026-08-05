# Total Wine — `total-wine`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `total-wine` |
| Runs | `import os, total_wine_full as m; m.run(os.environ.get('TW_STORE','920'), state=os.environ.get('TW_STATE','FL'))` |
| Module | `unifyd/os.py` |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `mac` |
| Cost class | mac |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** PerimeterX — browser. run() needs a storeId (national catalog, that store's price/stock); 920=Orlando Millenia is the documented default, override via TW_STORE/TW_STATE. (was run() -> TypeError)


## 2. Transport

_No literal endpoint constant in `os.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


## 3. What it lands


### `total_wine_products`

9,113 rows · 17 columns


| column | type | filled |
|---|---|---|
| `sku` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `size` | `VARCHAR` | 99.8% |
| `price` | `DOUBLE` | 7.0% |
| `category` | `VARCHAR` | 99.9% |
| `description` | `VARCHAR` | **0.1%** |
| `image` | `VARCHAR` | 79.6% |
| `url` | `VARCHAR` | 79.6% |
| `varietal` | `VARCHAR` | 99.9% |
| `origin` | `VARCHAR` | 100.0% |
| `region` | `VARCHAR` | 72.4% |
| `sub_region` | `VARCHAR` | **0%** ‹never populated› |
| `appellation` | `VARCHAR` | **0%** ‹never populated› |
| `style` | `VARCHAR` | 100.0% |
| `abv` | `VARCHAR` | 18.9% |
| `run_id` | `VARCHAR` | **0.1%** |

Fill measured over **full table** (9,113 rows).

> **2 columns never populated:** `sub_region`, `appellation`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `total_wine.py:202` (write_accumulate), `total_wine_full.py:44` (write_accumulate), `total_wine_inventory.py:269` (write_accumulate)


## 4. Module documentation

**`os.py` has no module docstring.** Everywhere else in this engine the docstring carries the rebuild narrative — the measurements behind the constants, the failure modes, the reason for the shape. Without it this source is only as legible as its code.


## 5. Raw source fields

Endpoint: `GET /product/api/product/product-detail/v1/getProduct/<skuId>?...&storeId=<S> (warmed PX cookie)` · grain: product × store


| raw field | meaning | maps to |
|---|---|---|
| `skuId` | SKU (productId-1) — NO UPC exists anywhere in the payload | `sku (upc always '')` |
| `stockLevel[].stock` | EXACT per-store on-hand units | `qty` |
| `stockLevel[].purchaseLimit` | per-order purchase cap | `purchase_limit` |
| `stockMessages.{digitalStoreQuantity,shippingStoreQuantity,digitalInStock,digitalLimitedStock}` | store + shipping quantities + in/limited-stock flags | `store_qty / shipping_qty / stock_level` |
| `price[].{price,type}` | price + type (EDLP) | `price / price_type` |
| `name / brand.{name,id}` | name + brand + brand id | `name / brand / brand_id` |
| `packageDescription / options[].value` | size ('1.75L Box') + package variants | `size` |
| `alcoholPercentage` | ABV | `abv` |
| `itemCharacteristics[] (FINISH/TASTE1-3/STYLE/BODY)` | RNDC attribute vector | `finish/taste/style/body` |
| `categories[] (VARIETAL_TYPE/COUNTRY_STATE/REGION/PRODUCT_TYPE)` | geo/type | `varietal/origin/region/style` |
| `review` | tasting notes + AWARD ('Gold - SIP Awards', '92 points') | `tasting_notes / award (parsed)` |
| `pairingsConfig[].options / productHighlights / tasteProfiles` | food pairings + taste descriptors | `pairings` |
| `customerAverageRating / customerReviewsCount` | ratings | `rating / reviews` |
| `lisaInfo[] / rawLocation / bay / shelf / location` | MULTI-LOCATION planogram (aisle + cooler door) | `bay / shelf / aisle / raw_location` |
| `productUrl / canonicalUrl` | URL | `url` |
| `images[].url` | image | `image` |
| `department / directType / salesStrategy` | dept + Spirits-Direct ship flag | `department / direct_ship` |
| `merchBadges[] (new)` | merch badges | `is_new` |
| `shoppingOptions[] / skus[] / breadCrumbs / metaDescription` | fulfillment / variants / nav | `raw_json` |


total_wine_inventory.py parse_product (audited a real getProduct). COUNTS (stock) + the fullest enrichment of any source — RNDC vector, awards, pairings, multi-location planogram — but NO UPC.
