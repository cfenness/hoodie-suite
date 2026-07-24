# Instacart — Scraper Handoff

> **Status: cracked, but not landing data yet.** The API and anti-bot are solved; the last mile
> (session automation + the alcohol login gate) is unfinished. Treat the fields below as the
> **target schema**, not a live feed. Not in the daily registry.

| | |
|---|---|
| **Status** | ⚠ Recon complete — NOT landing (parked, no active registry entry) |
| **Approaches** | A) direct persisted GraphQL via BD Browser · B) BD managed Instacart dataset |
| **Class** | `mac` / BD Browser (Forter + reCAPTCHA) |
| **Inventory signal** | in/out per store (NOT a bottle count) |
| **Key files** | `instacart.py` (direct GraphQL on the `aggregator.py` harness), `instacart_scraper.py` (BD dataset) |

## What we can accomplish

A near-complete grocery + convenience catalog with **per-store price and in/out availability** across
~20 grocery banners that unlock at once per zone. The three big aggregators (Instacart, DoorDash,
Uber Eats) are the same shape, so the shared `aggregator.py` harness is built once. Instacart is the
cleanest grocery data of the three — but it's also the most aggressively protected, and the
**alcohol business case specifically requires a logged-in, age-verified account** (see gotchas), so
bev-alc is gated behind an operational step the others aren't.

Realistic near-term deliverable: **non-alcohol** lands end-to-end as proof-of-pipe; bev-alc lands
once an age-verified account session is wired in and pointed at alcohol-serviceable states.

## Access & mechanism

- **Approach A (direct):** the site's own persisted GraphQL op `SearchResultsPlacements`
  (`sha256Hash 6f8d4a3f…23f4a`), driven through a **Bright Data Browser** session (naive local
  headless can't complete the zone-set interaction — Forter degrades it). Returns clean JSON at
  `data.searchResultsPlacements.placements[]`.
- **Approach B (managed):** Bright Data's managed Instacart dataset (Web Scraper API) — paid,
  ToS-gray; you feed store/category URLs and get structured records. Field names vary by dataset, so
  the connector self-reports `degraded` and dumps the first batch to `instacart_debug.json` to lock
  the mapping.

## Levels of pull (Approach A)

1. **Open session + set zone**
   `bdata browser open https://www.instacart.com/ --country us`
   A delivery zone auto-sets from the residential IP; the homepage lists real retailers with ETAs.
2. **Enter a store, capture zone params**
   `click a non-membership grocery → read {shopId, postalCode, zoneId}`
   Storefront context (shopId) + zone scraped from a live `SearchResultsPlacements` request URL.
3. **Search → items**
   `GET /graphql?operationName=SearchResultsPlacements&variables={query,shopId,zoneId,…}`
   Clean JSON → `Item*` nodes → name, price viewSection, size, image (see field table).

## Every field the connector is built to capture


**Approach A — direct persisted-GraphQL (`instacart.parse_item`)**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `retailer / store` | str | Retailer slug (the storefront). |
| `store_id` | str | `shopId` — per-retailer-per-zone shop id. |
| `zone` | str | `zoneId` — delivery zone (from the residential IP). |
| `product_id` | str | Item node id / legacyId. |
| `upc` | str | Empty — SearchResultsPlacements cards carry no barcode. |
| `brand` | str | Empty (not on the card). |
| `name` | str | Item name. |
| `category` | str | Empty (search-scoped). |
| `size` | str | Item size string. |
| `price` | float $ | First $x.xx from the price viewSection (priceString/fullPriceString). |
| `in_stock` | bool | True (search returns purchasable cards). |
| `image_url` | str | First product image. |
| `raw_json` | json str | The raw `Item*` node (≤4k). |

**Approach B — Bright Data managed dataset (candidate map, first-match-wins)**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `name` | str | name / title / product_name / productName. |
| `price` | float $ | price / current_price / sale_price / unit_price. |
| `store` | str | store / retailer / store_name / warehouse / location. |
| `id` | str | id / product_id / sku / item_id / upc / url. |
| `availability` | bool | availability / in_stock / available / stock_status. |

## Sample record (Approach A shape)

> **REPRESENTATIVE** — real field names & types, illustrative values. Non-alcohol proves the pipe; bev-alc needs an age-verified session.

```json
{
  "retailer": "publix",
  "store": "publix",
  "store_id": "16278",
  "zone": "1288",
  "product_id": "items_29481756",
  "upc": "",
  "brand": "",
  "name": "Tito's Handmade Vodka",
  "category": "",
  "size": "750 ml",
  "price": 22.99,
  "in_stock": true,
  "image_url": "https://www.instacart.com/image-server/....png",
  "url": "",
  "raw_json": "{\"__typename\":\"ItemUv2\",\"id\":\"items_29481756\",\"name\":\"Tito's Handmade Vodka\",\"size\":\"750 ml\",\"viewSection\":{\"priceString\":\"$22.99\"}}"
}
```

## Gotchas & hard-won learnings

- **Alcohol gate (the key business finding).** Anonymous alcohol queries return “Sorry, alcohol products aren't available” + a login prompt. Bev-alc needs a **logged-in, age-verified account session** (cookies injected into the BD browser) **and** an alcohol-serviceable state (CA/FL/…; some control states won't carry it regardless). Non-alcohol browses anonymously.
- **Anti-bot = Forter + reCAPTCHA.** Local headless Playwright loads pages but can't complete the zone-set interaction (address-autocomplete + “use current location” both time out). The browser layer must be the BD Browser API or an equally hardened stealth browser.
- **A delivery zone must be set before anything but page shells loads.** With BD Browser the zone auto-sets from the residential IP (no address dance).
- **Membership warehouses (Costco/Sam's) block the search op** → pick a regular grocery (ALDI/Target/Publix/Kroger/…).
- **Storefronts must be *entered*** (click the retailer) before `/store/<slug>/s?k=` works — a direct search URL redirects to the homepage.
- **shopId + zoneId only exist once a live `SearchResultsPlacements` call fires** — scrape them from `bdata browser network`, then re-issue with your own query terms.
- **Navigating directly to a raw graphql URL is flaky** (Forter flags it / “site can't be reached”) → retry, or let the search UI fire the call and read it via CDP.
- **Data is availability, not counts** — per-store price + in/out, not a bottle quantity.
- **Approach B field names vary by BD dataset** — the connector dumps raw + self-reports `degraded` on the first run so you can pin the mapping before trusting it.

## Path to landing (recommended sequence)

- Harden Approach A so **non-alcohol lands end-to-end** (proof-of-pipe) via the `aggregator.py` harness.
- Create/hold an **age-verified Instacart account**; inject its session cookies into the BD browser.
- Target alcohol-serviceable zones; call `SearchResultsPlacements` per bev-alc query → land via the harness.
- Then DoorDash & Uber Eats reuse the same harness (find their persisted op + zone + login).

## Files & entrypoints

- `unifyd/aggregator.py` — shared `AggregatorConnector` base (session/zone lifecycle, paged fetch, dedup, incremental landing).
- `unifyd/instacart.py` — Approach A: implements the harness hooks with all recon encoded (does not yet land).
- `unifyd/instacart_scraper.py` — Approach B: BD managed dataset (needs `BRIGHTDATA_API_KEY`, `BRIGHTDATA_INSTACART_DATASET`, `BRIGHTDATA_INSTACART_URLS`).


---
*Part of the Hoodie Suite scraper cutover pack. See [`README.md`](README.md) for the shared architecture, anti-bot infrastructure, and standing rules that apply to every source.*
