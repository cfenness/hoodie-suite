# Walmart — `walmart`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `walmart` |
| Runs | `import walmart_direct as m; m.pull(detail_pages=True, detail_cap=600)` |
| Module | `unifyd/walmart_direct.py` — 405 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | proxy |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi`, `patchright` |
| Unit test | **none** |


**Registry note.** walmart_direct: IPRoyal residential exit + curl_cffi Chrome-JA3, $0 (no BD, no API). A warmed WALMART_COOKIE is an OPTIONAL boost, NOT required — do not gate the run on it.


## 2. Transport

_No literal endpoint constant in `walmart_direct.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `browser_warm`, `observe`, `resi`, `warehouse`


## 3. What it lands


### `walmart_products`

7,324 rows · 36 columns


| column | type |
|---|---|
| `product_name` | `VARCHAR` |
| `item_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `offer_id` | `VARCHAR` |
| `price` | `DOUBLE` |
| `size_ml` | `DOUBLE` |
| `brand` | `VARCHAR` |
| `type` | `VARCHAR` |
| `image` | `VARCHAR` |
| `url` | `VARCHAR` |
| `category` | `VARCHAR` |
| `category_path` | `VARCHAR` |
| `rh_path` | `VARCHAR` |
| `product_type_id` | `VARCHAR` |
| `primary_shelf_id` | `VARCHAR` |
| `ironbank_category` | `VARCHAR` |
| `is_alcohol` | `BOOLEAN` |
| `varietal` | `VARCHAR` |
| `region` | `VARCHAR` |
| `vintage` | `VARCHAR` |
| `abv` | `DOUBLE` |
| `container` | `VARCHAR` |
| `flavor` | `VARCHAR` |
| `pairing` | `VARCHAR` |
| `wine_score` | `VARCHAR` |
| `aisle` | `VARCHAR` |
| `order_limit` | `BIGINT` |
| `store_id` | `VARCHAR` |
| `store_state` | `VARCHAR` |
| `store_city` | `VARCHAR` |
| `avg_rating` | `DOUBLE` |
| `num_reviews` | `BIGINT` |
| `rollback` | `BOOLEAN` |
| `seller` | `VARCHAR` |
| `in_stock` | `BOOLEAN` |
| `raw_json` | `VARCHAR` |


**Written by** `walmart_api.py:150` (write_accumulate), `walmart_direct.py:300` (write_accumulate)


## 4. `walmart_direct.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
walmart_direct.py — Walmart bev-alc DIRECT (no Bright Data). Walmart's desktop site is PerimeterX-walled
(307/418), but a MOBILE Safari UA walks right past it (same trick as Total Wine) and the search page embeds the
full product list in its `__NEXT_DATA__` JSON. So we paginate the bev-alc search terms, parse the items, and
land walmart_products + a national price observation — all on our own IP, $0.

Fields per item: name (size/ABV embedded → the master's parsers derive brand/size/abv), usItemId, price, image,
canonical URL, department/category. No UPC in search (Walmart doesn't expose it) — fine, matching rests on the
name-key + attribute vector, not UPC.

    python walmart_direct.py                     # default bev-alc terms
    python walmart_direct.py --terms "bourbon,tequila" --max-pages 5

Polite: mobile UA, delay + backoff, direct-first. BD stays only as an optional last resort (WALMART_BD=1).
```


## 5. Raw source fields

Endpoint: `GET /ip/… __NEXT_DATA__ .product  (mobile UA past PerimeterX, no BD)` · grain: product × store


| raw field | meaning | maps to |
|---|---|---|
| `usItemId` | Walmart item id | `item_id` |
| `upc` | UPC — the master match key (DETAIL only; search hides it) | `upc` |
| `id / offerId` | product/offer ids | `offer_id` |
| `name` | product name | `product_name` |
| `brand` | brand | `brand` |
| `type` | Walmart class (Wine/Beer/…) | `type` |
| `rhPath` | retail-hierarchy path 40000:42000:… (taxonomy) | `rh_path` |
| `category.path[]` | breadcrumb Food>Alcohol>Wine | `category_path` |
| `productTypeId / primaryShelfId / ironbankCategory` | taxonomy/shelf ids | `product_type_id/…` |
| `shortDescription` | marketing text — carries ABV ('6% ABV') + calories + packaging | `abv (parsed)` |
| `idml.specifications[]` | RNDC vector: varietal/region/vintage/ABV/container/flavor/pairing/wine_score | `varietal/region/vintage/abv/container/flavor/pairing/wine_score` |
| `productLocation[0].displayValue` | AISLE ('A34') — was missed (not a key named 'aisle') | `aisle` |
| `orderLimit / orderMinLimit` | per-order purchase cap | `order_limit` |
| `availabilityStatus` | IN_STOCK/OOS (no number) | `in_stock` |
| `location.{storeIds,city,stateOrProvinceCode}` | per-store context | `store_id/store_city/store_state` |
| `isLMPAlcoholItem / legalRestriction` | alcohol-marketplace + restriction flags | `is_alcohol` |
| `averageRating / numberOfReviews` | ratings | `avg_rating / num_reviews` |
| `badges.flags[] (ROLLBACK)` | Rollback promo badge | `rollback → on_promo` |
| `secondaryOfferPrice.currentPrice.price / priceInfo` | price | `price` |
| `sellerName / sellerType` | seller (Walmart.com/marketplace) | `seller` |
| `imageInfo.allImages[]` | images | `image` |
| `salesUnit / weightIncrement / promoData / discounts / returnAttributes` | misc | `raw_json` |


walmart_direct.py detail(). UPC + rhPath + aisle + ABV(from shortDescription) were the gaps a 'capture all of this' pass closed. Search page is thinner (no UPC); DETAIL has the full object.
