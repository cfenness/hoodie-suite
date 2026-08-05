# Stop & Shop — `stop-and-shop`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `stop-and-shop` |
| Runs | `import stop_and_shop as m; m.main([])` |
| Module | `unifyd/stop_and_shop.py` — 150 lines |
| Cadence | daily |
| Enabled | no — does not run on a cadence |
| Executor class | `mac` |
| Cost class | mac |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** needs a warmed cookie — not headless


## 2. Transport

| constant | value |
|---|---|
| `BASE` | `https://stopandshop.com/api/v6.0/products` |


**Depends on** `observe`, `warehouse`


## 3. What it lands


### `stop_and_shop_products`

**Has never landed.** `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/stop_and_shop_products.parquet' in region 'auto' (HTTP 404 Not Found)`

This is a registered source whose table does not exist in the warehouse — it has never completed a successful run, or it writes under a different name than the registry declares.


## 4. `stop_and_shop.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
stop_and_shop.py — Stop & Shop bev-alc catalog via the Ahold/Peapod product API.

Stop & Shop runs on the **Ahold Delhaize / Peapod** digital platform (shared with Giant Food, Giant/Martin's,
Hannaford, Food Lion), whose product-search API returns a rich per-product record — `response.products[]` with
UPC, aisle+section (planogram-lite), category tree, nutrition, bottle-deposit, and price. We take EVERYTHING it
serves (+ raw_json). No numeric on-hand — availability is a `flags.outOfStock` boolean.

  ⚠️ ToS: stopandshop.com/robots.txt DISALLOWS /api/ (the data lives there) — this is a ToS-sensitive source.
     Treat like the ask-first sources ([[bevalc-enrichment]]): confirm the call before running a live pull.
     The site is also anti-bot (plain fetch → block page), so a live pull needs a warmed session / BD Browser.
     `parse_product` itself is ToS-neutral — it just flattens a payload you already have.

Endpoint (Peapod v6): `GET /api/v6.0/products/<store>?text=<term>&flags=true&nutrition=true&substitutions=false`
(store-scoped; the pasted response shape is `response.products[]` + `response.pagination`). Generalizes to the
other Ahold banners by swapping the host. Lands `stop_and_shop_products` + observe.

    python stop_and_shop.py --cookie "$SS_COOKIE" --store <storeId> --terms "wine,beer,seltzer"
```


## 5. Raw source fields

Endpoint: `GET /api/v6.0/products/<store>?text=… (robots-DISALLOWED /api/; warmed session)` · grain: product × store


| raw field | meaning | maps to |
|---|---|---|
| `prodId` | Peapod product id | `prod_id` |
| `upc` | UPC — the master key | `upc` |
| `name / brand / brandId` | name + brand | `name / brand / brand_id` |
| `size / unitMeasure` | size ('750 ML BTL') + unit ('LTR') | `size / unit_measure` |
| `price / regularPrice / unitPrice / weightedRegularPrice` | prices | `price / regular_price / unit_price` |
| `aisle / section / pickStoreLocationId` | PLANOGRAM (aisle 9 / section 026 / '09B-026-001-002') | `aisle / section / pick_location` |
| `categoryPath[] / subcatName / rootCatName / subcatId` | category tree + subcat ('Non-Alcoholic Beer') | `category_path / subcat / root_cat` |
| `isAlcohol` | bev-alc flag | `is_alcohol` |
| `bottleDepositMap` | per-state bottle deposit {NY:0.05} | `bottle_deposit` |
| `nutrition.{totalCalories,servingSize,servingsPerContainer}` | nutrition | `calories/serving_size/servings` |
| `flags.outOfStock` | in/out bool — NO numeric count | `out_of_stock` |
| `hasCoupon / availableDisplayCoupons / advertiseOnSale / bmsm` | promo signals | `has_coupon / on_sale` |
| `ebtEligible / isMarketplaceProduct` | EBT / 3P flags | `ebt_eligible / marketplace` |
| `image.{small,medium,large}` | image | `image` |
| `rating / reviewId / guidingStars / sustainabilityRating / weightIncrement` | misc | `raw_json` |


stop_and_shop.py parse_product. Peapod platform → Giant/Hannaford/Food Lion by host. Rich master + planogram + nutrition + deposits; no count. ToS-sensitive (robots disallows /api/).
