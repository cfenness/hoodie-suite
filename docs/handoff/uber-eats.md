# Uber Eats — Scraper Handoff

> Bev-alc + grocery + restaurant capture from Uber's first-party BFF, via **our own real Chrome**
> — no Bright Data, no login. The deliverable is the **channel markup**: the aggregator price vs the
> retailer's direct price = the effective platform take-rate.

| | |
|---|---|
| **Status** | Live (per-zone module proven; national crawler launched, resumable) |
| **Registry id** | `ubereats` (Postmates = `postmates`, same code) |
| **Entrypoint** | `import ubereats as m; m.main(['--site','ubereats','--max-stores','1000'])` |
| **Class / cadence** | `mac` (anti-bot headful browser) / daily |
| **Lands to** | `ubereats_products` + `retail_observations` (channel=ubereats) |
| **Inventory signal** | Bounded on-hand proxy (`max_qty` = min(on-hand, buy-limit)) |
| **Key files** | `ubereats.py` (per-zone), `ue_crawl.py` (national), `ue_sitemap.py`, `ue_geofill.py`, `browser_warm.py`, `resi.py` |

## What we can accomplish

Full-catalog capture from any Uber Eats merchant — grocery, convenience, dedicated liquor, and
on-premise restaurant/bar drink menus — with **UPC, price, promo mechanics, a bounded on-hand
quantity, and (on-prem) recipe/customizations**. Because the retailer marks items up on the
aggregator to cover the platform fee, pairing an Uber Eats item with the same outlet's direct price
yields the **effective take-rate** — the reason to pull aggregators at all. Reconcile each merchant
to the same `hoodie_outlet` as the direct feed (ABC-from-Uber and ABC-direct = one outlet, two
channels); price signals must be **channel-aware** or the markup reads as an over-merge.

The **account universe** is separately and cheaply enumerable (see *Traversal* below) — ~285k US
merchants from the public sitemap, fully geocoded from each store page's JSON-LD, with **no browser
and no zone tiling**. Product/inventory capture is the expensive, zone-bound part.

## Access & mechanism

First-party BFF `POST https://www.ubereats.com/_p/api/*V1` (Postmates = same BFF on `postmates.com`).
Cracked 2026-07-16 with **no Bright Data and no login** — it's fingerprint + behavior, not auth.

## Levels of pull

1. **Zone feed — `getFeedV1`**
   `GET /feed?diningMode=DELIVERY&pl=<base64 location>`
   Merchant discovery for the zone. Yields slug, store uuid, name, storefront href from the `/store/<slug>/<uuid>` links (infinite-scrolled to load every merchant). The feed card also carries store-level promo/rating/ETA/open-closed — enough to track new outlets + store-wide promos daily without a getStore call.
2. **Store catalog — `getStoreV1`**
   `fires on a trusted click-through into a store`
   The item list. Title + price in flat keys; **list price, promo tag/uuid, the “SIZE • X% ABV” descriptor, and the stock-bucket label** live in nested `itemThumbnailElements` rich-text (strikethrough = list price) + `imageOverlayElements` tags. Carries everything **except the UPC**.
3. **Item detail — `getMenuItemV1`**
   `POST /_p/api/getMenuItemV1  (replayed per item)`
   The **richest** schema — the only level with the **UPC/GTINs** (`productIdentifiers[].value`), full promo mechanics (`itemPromotionV2`), the availability/quantity layer (`purchaseInfo.purchaseOptions[].quantityConstraintsV2.maxPermittedNumber`), unit/measure, and on-prem recipe/customizations. We learn the app's own request from one real click, then replay it per item (~3/s) to upgrade the whole catalog.

> **What `getMenuItemV1` is:** the per-item detail call the app fires when you tap a product tile.
> It returns the single richest view of one item and is the *only* level carrying the barcode. The
> catalog list (`getStoreV1`) is a lighter subset with no UPC and no quantity, which is why the
> crawler captures one real `getMenuItemV1` request template per store and replays it item-by-item.

## Every field we capture

*(Landed table `ubereats_products`, keyed by `store_uuid` + `item_uuid`. Postmates lands the identical schema to `postmates_products`.)*


**Identity**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `item_uuid` | str | Uber's per-item id at this store. |
| `product_uuid` | str | `productInfo.productUuid` — stable product id across stores. |
| `store_uuid` | str | Merchant id (the storefront). |
| `store_name` | str | Merchant display name. |
| `name` | str | Item title. |
| `section / subsection` | str | Catalog section/subsection uuids (aisle). |

**Barcodes**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `upc` | str | Best valid GTIN from `productIdentifiers[]` (digits only, check-digit sane). |
| `gtins` | str | **Every** GTIN on the item, pipe-joined — pack GTIN + unit GTIN + malformed twins. |

**Price & promo mechanics**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `price` | float $ | Current price (payload is in CENTS: 1641 → $16.41). |
| `list_price` | float $ | Struck-through was-price (flat key or catalog rich-text STRIKE_THROUGH). |
| `on_promo` | bool | list_price>price or any promo tag/uuid/type present. |
| `discount` | float $ | list_price − price when on promo. |
| `promo_text / promo_tag` | str | Promo description + storefront badge (“9% off”, “Deal”). |
| `promo_type` | str | Promotion type enum. |
| `promo_pct / promo_flat` | float | `offerInfo.discountPercentage` / `discountFlatAmount`. |
| `promo_uuid` | str | Applied/available promotion uuid. |

**Availability & quantity — closest thing to inventory Uber exposes**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `in_stock` | bool | Not sold-out and not suspended. |
| `is_sold_out` | bool | `isSoldOut` or itemAvailabilityState ∈ {SOLD_OUT, UNAVAILABLE}. |
| `suspend_reason / suspend_until` | str | Why/when the item is paused. |
| `low_availability` | str | `lowAvailabilityLabel` near stockout (“Only 3 left”). |
| `avail_state / stock_label` | str | itemAvailabilityState + qualitative bucket (“Many in stock”/“Few left”). |
| `max_qty` | int | `quantityConstraintsV2.maxPermittedNumber` = min(on-hand, buy-limit). A round default (100/250) = retailer does NOT sync inventory; a specific varied number tracks on-hand. |
| `min_qty / increment_qty / default_qty` | int | Quantity constraints. |
| `sold_by / priced_by` | str | Unit/measure (COUNT / WEIGHT / VOLUME). |

**Attributes**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `is_alcohol / num_alcoholic` | bool/int | Bev-alc flag (21+ rule, alcohol analytics label, or name match). |
| `age_rule` | str | Age-restriction text. |
| `abv / pack / item_size` | float/int/str | Parsed from nutritionalInfo/descriptor/title. |
| `nutritional_info` | str | “SIZE • X% ABV” descriptor. |
| `classifications / dietary_labels / endorsements` | str | Pipe-joined tags + badges. |
| `description` | str | itemDescription (≤400). |

**Media & provenance**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `image / image_count` | str/int | imageUrl + count. |
| `zone` | str | Delivery market the feed was primed to (e.g. orlando). |
| `raw_json` | json str | The **whole** getMenuItemV1 item object, pruned only of unrelated upsell blocks (≤16k). |

## Sample record

> **REPRESENTATIVE** — real field names & types, illustrative values. A bev-alc item after getStoreV1 + getMenuItemV1 enrichment.

```json
{
  "item_uuid": "b1e2f5a0-7c44-4a19-9d2e-3f0c1a8b6e21",
  "product_uuid": "0a9f3c72-11d8-42aa-bd1e-6c4e2f9a0b53",
  "store_uuid": "7f3c9d21-4e88-4b0a-9c1f-2a6b8d0e5f14",
  "store_name": "ABC Fine Wine & Spirits",
  "name": "Tito's Handmade Vodka 750 ml",
  "section": "9c2a...",
  "subsection": "5d71...",
  "upc": "619947000027",
  "gtins": "619947000027|00619947000027",
  "price": 24.99,
  "list_price": 27.99,
  "on_promo": true,
  "discount": 3.0,
  "promo_text": "$3 off",
  "promo_tag": "Deal",
  "promo_type": "ITEM_PRICE_PROMOTION",
  "promo_pct": null,
  "promo_flat": 3.0,
  "promo_uuid": "c4a1e8f0-2b33-4d90-8a11-7e5c9f2d1a04",
  "in_stock": true,
  "is_sold_out": false,
  "suspend_reason": "",
  "suspend_until": "",
  "low_availability": "",
  "avail_state": "AVAILABLE",
  "stock_label": "Many in stock",
  "max_qty": 24,
  "min_qty": 1,
  "increment_qty": 1,
  "default_qty": 1,
  "sold_by": "COUNT",
  "priced_by": "COUNT",
  "is_alcohol": true,
  "num_alcoholic": 1,
  "age_rule": "You must be 21 or older to purchase this item",
  "abv": 40.0,
  "pack": null,
  "item_size": "750 ml",
  "nutritional_info": "750 ml • 40% ABV",
  "classifications": "Spirits|Vodka",
  "dietary_labels": "",
  "endorsements": "Popular",
  "description": "Award-winning American vodka, distilled six times...",
  "image": "https://tb-static.uber.com/prod/image-proc/....jpeg",
  "image_count": 3,
  "zone": "orlando",
  "raw_json": "{\"uuid\":\"b1e2f5a0-...\",\"title\":\"Tito's Handmade Vodka 750 ml\",\"price\":2499,\"productIdentifiers\":[{\"type\":\"GTIN\",\"value\":\"619947000027\"}],\"purchaseInfo\":{...}}"
}
```

## Traversal & scale — how to get *every* account

The account universe and the product catalog are two different problems solved by two different
mechanisms. **Do not tile zones to enumerate accounts** — the feed only ever surfaces the ~45k
merchants deliverable to the zones you visit. The complete list comes from the public sitemap.

1. **Account universe (complete, cheap, headless) — `ue_sitemap.py`.**
   Uber publishes 26 gzipped store sitemaps (`sitemap-store-771af823-000…025.xml.gz`, listed in
   robots.txt, public + permitted). Every `/store/<slug>/<uuid>` URL is a merchant → **~285k US
   outlets** (uuid + name from the slug, no geo). `curl_cffi` + residential proxy, ~3 min, accumulates
   to `ubereats_sitemap`, merges to `src_outlets` via `sitemap_to_src_outlets()`. **This is “every
   account.”**
2. **Geocode the universe (headless) — `ue_geofill.py`.**
   Every `/store/` page embeds a JSON-LD `Restaurant` block with **exact geo + full PostalAddress +
   phone + priceRange + cuisine**. Fetch each page direct (home IP, `curl_cffi`), stream-stop after
   the JSON-LD (~125KB of 606KB). Resumable, threaded, circuit-breaker on velocity clamps, ISP-pool
   fallback. Lands `ubereats_geo` → merged to `src_outlets`. So sitemap (identity) + geofill
   (location) = every account fully attributed, **no browser, no zone tiling**.
3. **Coverage crawl (zone-bound, for the map + store-card promos) — `ue_crawl.py --coverage`.**
   Per zone, load `getFeedV1` once and harvest every merchant's `mapMarker` geo + card → `<site>_stores`.
   `run_ue_coverage_geo.py` runs this **nationally, parallel by state**, each state routed through a
   proxy IP *in that state* (a geo-mismatched exit IP returns an empty feed). Reads `zones_us.txt`
   (~230 metros; expandable to ZIP-level).
4. **Deep product crawl (zone-bound) — `ue_crawl.py --deep-stores`.**
   Drives the coverage store list; per store `getStoreV1` + `getMenuItemV1` for catalog + UPC/promo/
   recipe. Sharded/parallel with per-worker sticky proxy IPs (`--shard i/N`). Resumable + deduped.

**Two ID systems (gotcha):** the sitemap/URL id (from `/store/<slug>/<uuid>` and the feed's
`actionUrl`) is the **canonical account id**; the feed's `storeUuid` is a *different* id (the
getStoreV1 API key) kept as `api_uuid`. Dedup by URL id across sources — `reconcile_ue_ids.py` fixed
a real double-count from conflating them.

## Gotchas & hard-won learnings

- **Headless is dead.** Even patchright + a warmed profile hits Cloudflare “Just a moment — performing security verification” on the *feed itself*. Only a real *rendered* Chrome auto-clears the JS interstitial. In the cloud that means **Xvfb-headful** (real Chrome on a virtual framebuffer), never headless.
- **Real Chrome, not bundled Chromium.** `launch_persistent_context(channel="chrome")`. Chromium renders WebGL via SwiftShader — a bot tell reCAPTCHA flags; real Chrome uses the real GPU (ANGLE/Metal).
- **Human click-through from the feed.** Deep-linking a `/store/<slug>/<uuid>` URL trips reCAPTCHA's “One more step” (never clears). A trusted click on the store card from the feed clears it silently (`browser_warm.click_through`). In-session `p.goto(store_url)` works (native nav); replaying getStoreV1 for an arbitrary store 403s.
- **The `getMenuItemV1` 403 is NOT a token — it's missing headers.** Bulk POST replay 403s with a reCAPTCHA challenge until you replay the app's own `x-uber-*` headers (ciid, session-id, request-id, client-gitref, device/target-location) + `x-csrf-token:x`, captured from one real click, with a fresh `x-uber-request-id` uuid per call. Then 403→200 (proven 40/40 enriched).
- **Zone is sticky + needs a real place reference.** The `pl=` URL base64-encodes `{address, reference=uber_places id, referenceType, lat, lon}`; raw lat/lng FAILS (“no businesses”) — you need a valid `uber_places` reference from `mapsSearchV1`. The `uev2.loc` cookie persists it in the profile.
- **Catalogs are zone-bound.** `getStoreV1` returns a ~79-byte empty for an out-of-zone store even with location headers. National product coverage = enumerate US zones.
- **Home IP by default; a *flagged* proxy IP degrades the feed to ~1 merchant.** Uber was cracked from a home residential IP. Opt into a proxy with `UE_PROXY=1`. Off-Mac, route through a **state-matched** residential proxy (Webshare static) — a geo-mismatched exit IP returns an empty feed.
- **`max_qty` is a bounded proxy, not a count.** `maxPermittedNumber` = min(on-hand, buy-limit). A round default (100/250) means the retailer doesn't sync inventory; specific varied numbers = real on-hand. `lowAvailabilityLabel` (“Only N left”) populates near stockout.
- **Rapid repeat runs score the CF session down** (feed half-loads, store walls). Mitigated with retry-on-empty-feed + tolerant `goto`. Don't hammer.
- **Speed ceiling:** headful nav-per-store is ~10-20s/store — fine for a resumable metro crawl, can't touch the full universe. The unbuilt unlock is headless `getStoreV1` via `curl_cffi` (cold cookies + location headers returned a 73-byte error — the session-establishment work is the open speed project).
- **venv:** always `python -m playwright …` (the console-script shebangs point at the pre-iCloud path).

## Maintenance & health

The per-zone module (`ubereats.py`) is the proven core; `ue_crawl.py` reuses its helpers.
Runs land incrementally per zone and are **resumable** (dedup by `store_uuid|item_uuid`), which
matters because every Fly deploy restarts the runner. Watch the feed-render count — a zone that
renders <5 merchants is being scored down (challenge didn't clear / flagged IP), not empty.

## Files & entrypoints

- `unifyd/ubereats.py` — per-zone module: crawl → feed → click-through → getStoreV1 → getMenuItemV1; `parse_item()` (the 48-col landed schema) + `land()`.
- `unifyd/ue_crawl.py` — national orchestrator: `crawl_coverage` (feed geo), `crawl_zones` / `crawl_stores` (deep), geocode via `mapsSearchV1`.
- `unifyd/ue_sitemap.py` — the ~285k-outlet account universe from the store sitemaps.
- `unifyd/ue_geofill.py` — geocode the universe from each store page's JSON-LD (headless, direct).
- `unifyd/run_ue_national.sh` / `run_ue_coverage_geo.py` / `run_ue_deep_parallel.sh` — the runners.
- `unifyd/reconcile_ue_ids.py` — adopt the URL id as canonical account identity (dedup vs feed storeUuid).
- Run (one zone): `python ue_crawl.py --zones "Chicago, IL" --max-stores 300`


---
*Part of the Hoodie Suite scraper cutover pack. See [`README.md`](README.md) for the shared architecture, anti-bot infrastructure, and standing rules that apply to every source.*
