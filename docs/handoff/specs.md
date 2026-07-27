# Spec's — Scraper Handoff

> Spec's serves bots (200) and embeds a per-store `variants` object right in the product page — ~190
> store variants with in/out + price in **one fetch**. The numeric unit count is one hop away via an
> inventory API, making Spec's a true **Counts** source. Plain HTTP, no proxy.

| | |
|---|---|
| **Status** | Live |
| **Registry id** | `specs` |
| **Entrypoint** | `import specs_scraper as m; m.pull(crawl_all=True)` |
| **Class / cadence** | `headless` (stdlib, plain HTTP) / daily |
| **Lands to** | `specs_products` + `retail_observations` |
| **Inventory signal** | Exact per-store units |
| **Key files** | `specs_scraper.py` |

## What we can accomplish

The full ~41k-product catalog with **full product detail** (type/brand/abv/region/varietal/vintage/
tasting notes) + **per-store price and in/out for ~190 stores in one page fetch**, plus a **numeric
per-store unit count** from the inventory API. Per-store price *varies* at Spec's, so it's a genuine
price-dispersion source. UPC is recovered for free from the image filename.

## Access & mechanism

`specsonline.com` — plain HTTP, no proxy needed (it serves bots). Product universe from the sitemap;
per-store price/in-out from an embedded (escaped) JSON `variants` object on each PDP; the unit count
from `GET /api/products/stock/{storeCode}-{upc}/` → `{status:"ok", available:N, tracked:bool}`.

## Levels of pull

1. **Catalog (sitemap)**
   `GET /sitemap.xml → product child sitemaps (~41k products across 21 sitemaps)`
   Every product as (slug, URL).
2. **Product page (PDP)**
   `GET <product URL>`
   The **full product detail** (type/brand/abv/region/varietal/vintage/tasting-notes/pairs-with + ld+json name/description/price/image + **UPC parsed from the image filename**) **and** the embedded per-store variants block (store, sku, inStock, unitPrice).
3. **Inventory API (unit count)**
   `GET /api/products/stock/<storeCode>-<upc>/  → {status, available:N, tracked}`
   The **numeric units on hand** per (in-stock store, product) — the count the variants block omits. Fanned out one call per in-stock store.

## Every field we capture

*(Two grains: product detail + per-store cell.)*


**Product detail (table `specs_products`, keyed `sku|slug`)**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `sku / slug / url` | str | Product SKU, URL slug, PDP URL. |
| `name / brand / type / varietal` | str | From the PDP attribute block + ld+json. |
| `abv / origin / region / state / vintage` | str | ABV, country, “Kentucky (KY)”, 2-letter state, vintage. |
| `tasting_notes / pairs_with / description` | str | PDP attributes + ld+json description. |
| `price` | float $ | ld+json offers.price (each-price). |
| `upc` | str | **Parsed from the product image filename** — Spec's names images by UPC. |
| `image` | str | Product image. |

**Availability roll-up**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `in_stock_stores / store_count` | int | Stores in stock / listed (from the variants block). |
| `units_total / stores_tracked` | int | Total on-hand across counted stores + how many returned a count. |
| `raw_json` | json str | The untouched attribute + offers blocks (≤8k). |

**Per-store cell (lands to `retail_observations`)**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `store` | str | Store code. |
| `price` | float $ | Per-store unitPrice (cents→$). **Varies by store.** |
| `instock` | bool | Per-store inStock from the embedded variants block. |
| `qty` | int | **Numeric units on hand** via `/api/products/stock/<store>-<upc>/` → available. None when the product isn't inventory-tracked at that store. |

## Sample records

> **REAL** — pulled from a live snapshot. Product-level values (Blue Ice Huckleberry Vodka) — note the UPC recovered from the image filename.

```json
{
  "sku": "081519700328",
  "slug": "blue-ice-vodka-huckleberry",
  "url": "https://specsonline.com/....",
  "name": "Blue Ice Vodka • Huckleberry",
  "brand": "Blue Ice",
  "type": "Vodka",
  "abv": "40%",
  "origin": "United States",
  "region": "Idaho (ID)",
  "state": "ID",
  "price": 20.99,
  "upc": "081519700328",
  "image": "https://specsonline.com/images/...081519700328.jpg",
  "in_stock_stores": 42,
  "store_count": 190,
  "units_total": 318,
  "stores_tracked": 42,
  "raw_json": "{...}"
}
```

> **REAL** — pulled from a live snapshot. A per-store cell (CUNE Rioja Reserva, store 15) — **$34.41 here vs $34.73 / $35.78 at other stores** that run: Spec's prices vary by store. `qty` comes from the inventory API.

```json
{
  "store": "15",
  "slug": "c-v-n-e-...-vina-real-reserva-rioja",
  "sku": "",
  "name": "CUNE Viña Real Reserva Rioja",
  "price": 34.41,
  "instock": true,
  "qty": 6,
  "upc": "",
  "brand": "CUNE (C.V.N.E.)"
}
```

## Gotchas & hard-won learnings

- **UPC from the image filename** — Spec's names product images by UPC (`081519700328.jpg`). A reusable trick: retailer image CDNs often encode the UPC.
- **The count endpoint fans out per in-stock store** (~174 stores/product, one call each). A full crawl with counts on every store is a lot of requests → set `SPECS_COUNT_STORES="0,5,35"` to restrict counts to a focus set (`SPECS_QTY=1` default toggles the count hop). The embedded variants block still gives in/out + price for all ~190 stores in the single page fetch.
- **Catalog was silently half-truncated** — the old default capped harvest at 20,000 of ~41k. Now the whole catalog; a deliberate cap (`SPECS_MAX_PRODUCTS`) that bites is logged, not silent.
- **Shrink guard (learned the hard way, 2026-07-21):** a full crawl died mid-run and its OVERWRITE clobbered 40,689 rows → 163 (the empty-guard only stops 0-row writes). Now a “full” result under **70%** of the existing catalog ACCUMULATES instead of overwriting — the touched products update, the rest survive.
- **Known data-quality quirks** (caught by `normalization_scout.py`): brand mojibake and numeric-as-text price/sku columns. These are healed at the translation layer, never rewritten in the landed table.
- **Self-reports `degraded`** if the per-store `variants` block parses on <50% of pages (markup drift).

## Maintenance & health

Concurrent per-page fetch (12 workers default) + snapshot keyed `sku|store` diffed vs prior. The full
crawl OVERWRITES `specs_products` (authoritative catalog) but only past the 70% shrink guard;
sample/partial runs accumulate. Watch the parse-success rate (the degraded trigger) and the `qty`
fill-rate (how many stores returned a tracked count).

## Files & entrypoints

- `unifyd/specs_scraper.py` — `harvest_ids()`, `parse_stores()` (variants block), `parse_product()` (full detail), `fetch_store_qty()` (unit count), `pull()`.
- Run: `python specs_scraper.py --all` (full ~41k)  ·  `--sample 30` (deterministic spread).


---
*Part of the Hoodie Suite scraper cutover pack. See [`README.md`](README.md) for the shared architecture, anti-bot infrastructure, and standing rules that apply to every source.*
