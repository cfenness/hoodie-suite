# Binny's — Scraper Handoff

> Store-level price + inventory via Binny's **Algolia** index (public search key, client-exposed by
> design). Each product record carries a per-store array with a **numeric unit count** and per-store
> prices — the same call the site's search makes. No scraping, no Bright Data.

| | |
|---|---|
| **Status** | Live |
| **Registry id** | `binnys` |
| **Entrypoint** | `import binnys_scraper as m; m.pull(crawl_all=True)` |
| **Class / cadence** | `headless` (Algolia feed) / daily |
| **Lands to** | `binnys_products` + `retail_observations` |
| **Inventory signal** | Exact per-store units (`purchaseAvailability`) |
| **Key files** | `binnys_scraper.py` |

## What we can accomplish

The full ~31k-product catalog with **per-store price + exact units on hand**, and the day-over-day Δ
of `purchaseAvailability` per (sku, store) as a **directional units-sold** proxy. Each Algolia hit is
57 fields (all kept in `raw_json`), so beyond name/price we get varietal, region, country, proof→ABV,
case pack, ratings, and deal/discount — rich master fuel from one free API.

## Access & mechanism

`POST https://<appId>-dsn.algolia.net/1/indexes/Products_Production/query` with the public
`X-Algolia-*` headers (app id `Z25A2A928M`, index `Products_Production`, search-only key
`88b6125855a0bbd845447e35de8d51c5` — all env-overridable). Paginated 1000 hits/page. Direct, no proxy.

## Levels of pull

1. **Algolia query (paginated)**
   `POST https://<appId>-dsn.algolia.net/1/indexes/Products_Production/query  (1000/page, ~31k products)`
   One rich hit per product — **57 source fields** (name, brand, varietal, region, proof, THC/CBD mg, case pack, ratings, deal/discount, descriptions)…
2. **Per-store fan-out**
   `each hit's `storesPriceAndInventory[]`  →  cell keyed `sku|storeCode``
   …exploded into one cell per (product, store) carrying **per-store price + exact units on hand**. Lands `binnys_products` (keyed sku+store) + the dated observation series.

## Every field we land

*(Table `binnys_products`, keyed by `sku` + `store`.)*


**Identity & classification**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `sku / store` | str | Algolia objectID + store code (per-store cell). |
| `name / brand / varietal` | str | productName / productBrandName / productVarietal. |
| `region / origin` | str | region / country. |
| `category / department` | str | productType / gtmCategory / productDepartment (Beer/Wine/Spirits). |

**Pack & potency**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `item_size / unit_label / case_pack` | str/int | itemSize (“750ML”, “4 x 16Z”), priceUnitLabel, casePack. |
| `proof / abv` | float | proof; abv derived = proof/2. |
| `thc_mg / cbd_mg` | float | thcMg/cbdMg per serving/unit/sellpack — the hemp dose vendors are often null on (schema-present, empty at Binny's). |

**Price, inventory & ratings — per store**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `price` | float $ | Per-store price — salePrice when on sale, else regularPrice. |
| `qty` | int | **Numeric per-store units** — `storesPriceAndInventory[].purchaseAvailability`. Day-over-day Δ ≈ units sold. |
| `rating / reviews` | float/int | ratingNumber / reviewsAmount. |
| `discount_pct / deal_of_week` | float/bool | pricePercentDiscount / isDealOfTheWeek. |
| `is_sold_out / in_store_only` | bool | isSoldOut / isInStoreOnly. |

**Content & provenance**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `is_hemp / short_desc` | bool/str | Hemp classifier + shortDescription. |
| `product_url / image` | str | productUrl / imageUrl. |
| `raw_json` | json str | The **whole** Algolia hit minus `_highlightResult` — all 57 source fields (≤6k). |

## Sample record

> **REAL** — pulled from a live snapshot. A Saint-Émilion future at store 23: **$32.00, 981 units** on hand, **14.5% ABV** (from proof 29). `raw_json` (truncated here) keeps all 57 Algolia fields.

```json
{
  "name": "Chateau Tour Saint Christophe Saint Emilion (Futures) 2025",
  "brand": "Chateau Tour St Christophe",
  "varietal": "Red Bordeaux Blend",
  "region": "Saint Emilion",
  "origin": "France",
  "category": "Red Wine",
  "department": "Wine",
  "item_size": "750ML",
  "unit_label": "750 ml Bottle",
  "case_pack": 1,
  "image": "https://www.binnys.com/globalassets/catalogs/binnys/17/1781/178144/178144.jpg?v=639135753380000000",
  "product_url": "https://www.binnys.com/wine/red-wines/other-red-wines/chateau-tour-saint-christophe-saint-emilion-futures-178144/",
  "proof": 29.0,
  "abv": 14.5,
  "thc_mg": null,
  "cbd_mg": null,
  "rating": null,
  "reviews": null,
  "discount_pct": 0.0,
  "deal_of_week": false,
  "is_sold_out": false,
  "in_store_only": true,
  "short_desc": "Chateau Tour Saint Christophe Saint Emilion (Futures) 2025",
  "is_hemp": false,
  "raw_json": "{\"pointsMax\":96,\"objectID\":\"178144\",\"country\":\"United States\",\"gtmCategory\":\"beer\",...}",
  "price": 32.0,
  "qty": 981,
  "store": "23",
  "sku": "178144"
}
```

## Gotchas & hard-won learnings

- **Public search key is client-exposed by design** — the same key the site's search JS uses. Safe to persist; env-overridable if it rotates.
- **`storesPriceAndInventory[].purchaseAvailability` is the prize** — a real numeric per-store count, not just in/out. The scraper self-reports `degraded` if numeric qty is present on <50% of cells (schema change) or if the Algolia query fails (key rotated → re-discover from a product page).
- **proof → ABV** (abv = proof/2). **THC/CBD mg fields exist in the schema but are empty at Binny's** — they matter for the hemp vendors, not here.
- **Historically the scraper wasn't landing at all** — it computed snapshots but never wrote `binnys_products` / `observe`. Fixed; keep the warehouse land + `write_accumulate` (keyed `sku,store`).
- **Full catalog pagination** — was silently capped at 40 Algolia pages (truncated the catalog); now 100. `--all` for the full ~31k; `--sample N` for a quick pull.
- **Binny's also appears in `ab_outlets`** (the AB InBev national retailer locator, since it sells their beer in IL) — an outlet-side cross-reference, not a second product source.

## Maintenance & health

Snapshot keyed `sku|store`; the headline delta is `units_moved` (net depletion since last run). The
numeric-qty fill-rate is the drift signal. Because it's a persistent growing catalog, it **must** use
`write_accumulate` — a `--limit`/`--sample` run would otherwise clobber the full catalog.

## Files & entrypoints

- `unifyd/binnys_scraper.py` — `query()`, `to_snapshot()` (57-field capture → per-store cells), `diff_store()` (units_moved), `pull()`.
- Run: `python binnys_scraper.py --all` (full ~31k)  ·  `--sample 300` (quick).


---
*Part of the Hoodie Suite scraper cutover pack. See [`README.md`](README.md) for the shared architecture, anti-bot infrastructure, and standing rules that apply to every source.*
