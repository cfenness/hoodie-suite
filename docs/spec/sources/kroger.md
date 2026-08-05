# Kroger (atlas inventory) — `kroger`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `kroger` |
| Runs | `import kroger_atlas as m; m.main([])` |
| Module | `unifyd/kroger_atlas.py` — 294 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `mac` |
| Cost class | mac |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `patchright` |
| Unit test | **none** |


**Registry note.** INTERNAL atlas endpoint = exact per-store on-hand + dims + ABV; Akamai cookie AUTO-WARMED per run (cookie_warm headful Chrome — no manual paste); store 01100439/fac 14732 default


## 2. Transport

| constant | value |
|---|---|
| `API` | `https://www.kroger.com/atlas/v1/product/v2/products` |


**Depends on** `browser_warm`, `observe`, `warehouse`


## 3. What it lands


### `kroger_atlas_products`

**Has never landed.** `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/kroger_atlas_products.parquet' in region 'auto' (HTTP 404 Not Found)`

This is a registered source whose table does not exist in the warehouse — it has never completed a successful run, or it writes under a different name than the registry declares.


## 4. `kroger_atlas.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
kroger_atlas.py — Kroger's INTERNAL 'atlas' product API: the rich per-GTIN payload the site/app uses.

The public Developer API (kroger_api.py) is thin. The internal atlas endpoint
`GET /atlas/v1/product/v2/products?filter.gtin13s=…&projections=items.full,…` returns FAR more per product —
a trove of MASTER + ENRICHMENT data keyed by GTIN — AND, when the response carries the populated inventory
objects, an EXACT per-store on-hand count (`inventorySummaries[].details[].availableToSell` /
`inventory.locations[].available`; some responses have it empty + only the HIGH/LOW enum):

  • dimensions {height,width,length} + gtin14  → BOTTLE DIMENSIONS keyed by GTIN (feeds bottle_dims/master —
    authoritative + free, vs the vision/label derivation). For a bottle width==length==diameter.
  • romanceDescription                          → ABV ("8.5% alcohol by volume") — a field TTB detail lacks.
  • familyTree / taxonomies                     → Kroger commodity/department/subCommodity codes (dictionary).
  • location.locations[]                        → planogram: aisle + numOfFacings (multiple bays), feeds planogram.
  • per-modality availability (PICKUP/DELIVERY/IN_STORE): inventoryLevel HIGH/LOW + sellable + pricing.
  • dsdItem (Direct Store Delivery), brand+code, prop65, restrictionGroupCodes, ratings.

ACCESS: gated by the `x-laf-object` request header. The shape (reverse-engineered from a real response) is an
array whose modality carries the store: `[{"listingKeys":["<storeId>"],"modality":{"type":"PICKUP",
"handoffLocation":{"facilityId":"<fid>","storeId":"<storeId>"}}}]` — the server reads
`laf[0].modality.handoffLocation.storeId`, which is why {banner,storeId,modality} replays 400'd. Needs a warmed
session cookie (Kroger is anti-bot + our Claude-in-Chrome tools are domain-blocked). Provide via
`--cookie`/`KROGER_COOKIE` + `--store`/`--facility` (from the DD_modStore cookie / a store lookup).

Lands `kroger_atlas_products` (full snapshot + raw_json) and feeds the dimensions into the enrichment. stdlib.

    python kroger_atlas.py --cookie "$KROGER_COOKIE" --store 01100439 --facility 14732 --gtins 0008312001225
```


## 5. Raw source fields

Endpoint: `GET /v1/products (official OAuth2 Developer API)` · grain: product × store


| raw field | meaning | maps to |
|---|---|---|
| `productId` | Kroger product id | `product_id` |
| `upc` | UPC | `upc` |
| `brand` | brand | `brand` |
| `description` | product name/description | `name` |
| `categories[]` | category list | `category` |
| `items[].itemId` | item id | `sku` |
| `items[].size` | size | `size` |
| `items[].price.regular` | regular price | `price` |
| `items[].price.promo` | promo price | `promo` |
| `items[].inventory.stockLevel` | HIGH \| LOW \| TEMPORARILY_OUT_OF_STOCK — COLLAPSED count | `stock_level` |
| `items[].fulfillment` | curbside/delivery/inStore/shipToHome bools | _raw_json only_ |
| `aisleLocations[]` | aisle/shelf | `raw_json` |
| `images[]` | images | `image` |


RESOLVED: no raw numeric count anywhere in Kroger web/app (internal API also gives inventoryLevel HIGH/LOW). Internal atlas API is still worth pulling for planogram + per-modality availability + richer pricing. A true count would need a side channel (Instacart/Shipt 'only N left') or Kroger's internal ordering systems — out of web scope.
