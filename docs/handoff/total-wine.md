# Total Wine — Scraper Handoff

> Total Wine's **own `getProduct` JSON API** — the call its product pages use — returns **exact
> per-store unit counts + physical shelf position** (better than ABC's in/out), plus ABV, geo, and
> reviews. The catch is PerimeterX: the cookie is IP-bound, so warm + pull must share one clean IP.

| | |
|---|---|
| **Status** | Live |
| **Registry id** | `total-wine` |
| **Entrypoint** | `import total_wine_full as m; m.run()` |
| **Class / cadence** | `mac` (PerimeterX browser) / daily |
| **Lands to** | `total_wine_products` + `retail_observations` |
| **Inventory signal** | Exact units on hand + shelf bay |
| **Key files** | `total_wine_inventory.py` (the pull), `total_wine_full.py` (batched driver), `total_wine.py` (sitemap) |

## What we can accomplish

Per-store, per-SKU **exact inventory + price + shelf planogram** for the full ~143k catalog. One API
call supersedes the old 1.2MB page scrape: it gives catalog + geo (varietal/country/region) + ABV +
inventory + price + physical bay/shelf. It is a true **Counts** source. The full catalog is
resumable and shardable, so wall-clock scales with the number of parallel BD Browser IPs.

## Access & mechanism

`GET https://www.totalwine.com/product/api/product/product-detail/v1/getProduct/<skuId>?shoppingMethod=INSTORE_PICKUP&state=US-<ST>&attrConfig=true&storeId=<storeId>`
— the product universe comes from the published sitemap (`/p/<productId>` → skuId `<productId>-1`).
robots-compliant (`/p/`, the product sitemap, and `/product/api/…/getProduct` are all allowed;
`/search/` is not, so we never use a bulk search API).

**Two fetch modes** (PerimeterX binds cookie↔IP, so warm + pull share one IP):
- `TW_FETCH=direct` — **$0**: warm a PX cookie in a local browser once, then `requests.Session`
  direct on a **clean IP** (e.g. the Fly box). No Bright Data.
- `TW_FETCH=bdbrowser` — pull **through** one live BD Browser session via an in-page `fetch` (reuses
  its IP + warmed cookie). One session serves the whole batch (amortized BD).

## Levels of pull

1. **Product universe (sitemap)**
   `GET /sitemap → /p/<productId>  →  skuId “<productId>-1”`
   Every product URL (each size variant is its own /p/ URL).
2. **Per-store product detail (getProduct)**
   `GET /product/api/product/product-detail/v1/getProduct/<skuId>?shoppingMethod=INSTORE_PICKUP&state=US-<ST>&attrConfig=true&storeId=<storeId>`
   The full per-store record: **price, exact units on hand, ABV, physical bay/shelf/aisle, geo categories, characteristics, reviews and awards**. Lands two grains — a dated per-store observation (with qty) + a `total_wine_products` catalog enrichment row.

## Every field we capture

*(Per-store observation grain — the richest. `enrich_rows()` lands a ~29-col subset to `total_wine_products`.)*


**Store & identity**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `store_id / store` | str | Total Wine storeId (920 = Orlando Millenia) + label. |
| `product_id / sku` | str | skuId (“<productId>-1”). |
| `upc` | str | **Always empty** — getProduct exposes no UPC field (by design). |
| `brand / brand_id / name` | str | Brand + product name. |

**Price & inventory — exact unit counts**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `price / price_type` | float/str | First `price[].price` + type (EDLP/regular/sale). |
| `in_stock` | bool | digitalInStock or shippingInStock and not unavailableAtStore. |
| `qty` | int | **Exact units on hand** — `stockLevel[0].stock` (fallback digitalStoreQuantity). |
| `store_qty / shipping_qty` | int | stockMessages.digitalStoreQuantity / shippingStoreQuantity. |
| `purchase_limit` | int | stockLevel[0].purchaseLimit. |
| `stock_level` | str | OOS / LIMITED / IN. |

**Attributes & planogram**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `size / abv` | str/float | packageDescription / alcoholPercentage. |
| `bay / shelf / aisle / raw_location` | str | Physical shelf position (e.g. `05.L.05.01`) + full multi-location planogram. |
| `finish / taste / body` | str | itemCharacteristics (RNDC-style vector; TASTE1/2/3 joined). |
| `style / varietal / origin / region` | str | categories[] by type (PRODUCT_TYPE / VARIETAL_TYPE / COUNTRY_STATE / REGION). |
| `tasting_notes / award` | str | review text + medal/points parsed from it. |
| `pairings / rating / reviews` | str/float/int | Food pairings + customer ratings. |
| `department / direct_ship / is_new` | str/bool | Department, ship type, new-badge. |

**Media & provenance**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `url / image` | str | productUrl + first image. |
| `is_hemp / raw_json` | bool/json | Hemp classifier + whole getProduct object (≤6k). |

## Sample record

> **REPRESENTATIVE** — real field names & types, illustrative values. A Napa cab at store 920. `upc` is empty because Total Wine never provides one.

```json
{
  "store_id": "920",
  "store": "Total Wine - Orlando",
  "product_id": "223968750-1",
  "sku": "223968750-1",
  "upc": "",
  "brand": "Caymus",
  "brand_id": "12841",
  "name": "Caymus Cabernet Sauvignon",
  "price": 89.99,
  "price_type": "regular",
  "in_stock": true,
  "qty": 48,
  "store_qty": 48,
  "shipping_qty": 120,
  "purchase_limit": 12,
  "stock_level": "IN",
  "size": "750ml",
  "abv": 14.8,
  "bay": "A12",
  "shelf": "3",
  "aisle": "Cabernet Sauvignon",
  "raw_location": "05.L.05.01",
  "finish": "Long",
  "taste": "Black Cherry, Vanilla, Mocha",
  "body": "Full",
  "tasting_notes": "Rich and layered with dark fruit, baking spice and a supple finish...",
  "award": "94 points - Wine Spectator",
  "pairings": "Beef, Lamb, Hard Cheese",
  "rating": 4.7,
  "reviews": 1203,
  "department": "Wine",
  "direct_ship": "",
  "is_new": false,
  "url": "https://www.totalwine.com/wine/red-wine/cabernet-sauvignon/caymus-cabernet/p/223968750",
  "image": "https://www.totalwine.com/media/....png",
  "is_hemp": false,
  "raw_json": "{...}",
  "style": "Red Wine",
  "varietal": "Cabernet Sauvignon",
  "origin": "United States",
  "region": "Napa Valley"
}
```

## Gotchas & hard-won learnings

- **No UPC — by design.** getProduct carries no barcode field; `upc` stays empty. Identity is skuId + brand/name/size/geo.
- **PerimeterX binds the cookie to the IP.** A bare BD-Unlocker GET returns empty. Warm a PX cookie once, then pull from the **same IP**. The API also needs an **in-page `fetch`** — an external replay 403s even with a warmed `_px` cookie because the app adds a dynamic PX header; `page.request` does NOT replicate the PX/session context.
- **IPRoyal (cheap P2P residential) is blocked at IP-reputation.** Total Wine's PX hard-denies IPRoyal's P2P range across IPs, headless and headful. Needs **premium residential** (Bright Data / Oxylabs) or BD Unlocker. (Contrast: Walmart's softer PX clears on IPRoyal — start cheap, escalate only where proven necessary.)
- **Don't hammer — the dev IP gets flagged.** Aggressive concurrent testing flagged the free mobile-UA page path (serial then 25/25 blocked). Keep it gentle; the batched driver re-warms a fresh PX session per batch.
- **Resumable + shardable.** `total_wine_full.run()` skips skuIds already in `total_wine_products` and processes disjoint slices with `SHARDS=N SHARD=i` (independent BD Browser IPs → ~N× wall-clock). A near-empty batch = a dead PX session; the next batch re-warms.
- **`getProductList` (browse) is the bulk route** — returns many products-with-stock per store/category in one call (vs one getProduct per SKU).

## Maintenance & health

Watch the per-batch yield: a batch under ~5% of its size = a silently dead PX session (empties read
as “no product”, not “blocked”) — the driver logs it loudly and the next batch self-heals. The
`qty`/`abv` fill-rates are the drift signals. `total_wine.py`'s old HTML/microdata parser is
**obsolete** — this API supersedes it; don't resurrect the page scrape.

## Files & entrypoints

- `unifyd/total_wine_inventory.py` — `parse_product()` (the full per-store record), `_pull_bdbrowser` / `_pull_direct`, `enrich_rows()`.
- `unifyd/total_wine_full.py` — batched, resumable, shardable driver (fresh PX warm per batch). The registry entrypoint.
- `unifyd/total_wine.py` — sitemap product-URL universe.
- Run: `TW_FETCH=direct python total_wine_full.py --store 920 --state FL`  ·  5-way: `SHARDS=5 SHARD=0..4`


---
*Part of the Hoodie Suite scraper cutover pack. See [`README.md`](README.md) for the shared architecture, anti-bot infrastructure, and standing rules that apply to every source.*
