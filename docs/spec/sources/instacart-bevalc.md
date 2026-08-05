# Instacart — bev-alc (session-gated) — `instacart-bevalc`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `instacart-bevalc` |
| Runs | `import instacart as m; r = m.Instacart().pull(address='10001', retailers=['grocery'], queries=['vodka','wine','whiskey','beer','tequila'], per_query_pages=2); print(len(r))` |
| Module | `unifyd/instacart.py` — 248 lines |
| Cadence | daily |
| Enabled | no — does not run on a cadence |
| Executor class | `mac` |
| Cost class | free |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | `INSTACART_SESSION_COOKIES` |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** ONE zone / a few alcohol terms — proves whether a plain logged-in session lifts the anonymous alcohol gate. No proxy (free self-hosted browser, per instacart.py; the driver is patchright — the image has no playwright). Manual trigger only.


## 2. Transport

| constant | value |
|---|---|
| `GRAPHQL` | `https://www.instacart.com/graphql` |


**Depends on** `aggregator`, `browser_warm`


## 3. What it lands


### `instacart_products`

**Has never landed.** `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/instacart_products.parquet' in region 'auto' (HTTP 404 Not Found)`

This is a registered source whose table does not exist in the warehouse — it has never completed a successful run, or it writes under a different name than the registry declares.


## 4. `instacart.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
instacart.py — Instacart connector on the aggregator harness (see aggregator.py).

STATUS (2026-07-22): FREE PATH. The browser DRIVER is now a self-hosted Chromium — NO Bright Data,
NO proxy. A cloud probe (`instacart_free_probe.py`, run on a bare datacenter runner) proved a real Chromium
reaches Instacart's homepage → a grocery storefront → the product GraphQL with no anti-bot block and no paid
layer (home=True blocked=False store=True search_gql=True products=76). The data is Instacart's own persisted
GraphQL — the browser is only how we drive a real session; it never needed to be a paid one.

The recipe (unchanged from the BD era — only the driver changed):
  • ZONE: a delivery zone = {shopId, postalCode, zoneId}. It rides in the `variables` of every live
    `SearchResultsPlacements` request. We ENTER a non-membership grocery storefront (membership warehouses
    wall the product query), run one seed search, and read the three ids back out of the captured request URL.
  • PRODUCT API: persisted GraphQL
      GET https://www.instacart.com/graphql?operationName=SearchResultsPlacements
          &variables={query, shopId, postalCode, zoneId, first, orderBy:"bestMatch", ...}
          &extensions={persistedQuery:{version:1, sha256Hash:SEARCH_HASH}}
    Returns clean JSON at data.searchResultsPlacements.placements[]. We replay it per query term with our own
    zone — no browser interaction per page, just a navigation to the graphql URL and a body read.
  • ALCOHOL GATE (verified in PA + LA): alcohol needs a logged-in, age-verified session — anonymous returns
    "alcohol products aren't available". NON-alcohol browses anonymously, so the free anon path is the
    proof-of-pipe + the whole non-alc long tail; bev-alc later needs an account session injected here.

Driver notes (free self-hosted browser):
  • DRIVER RESOLUTION: never `from playwright...` — the Fly image installs patchright and NOT playwright, so a
    direct import is a ModuleNotFoundError on the first real run (it was exactly that, latently, here). _launch
    goes through `browser_warm.sync_playwright_api()`, the one shared resolver. See browser_driver_test.py.
  • Headful by default under Xvfb (BROWSER_HEADFUL unset → headless=False) — matches the probe; the toughest
    fingerprinting sometimes needs a real window, and a datacenter Xvfb window cleared Instacart in the probe.
    Set BROWSER_HEADFUL=0 to force headless.
  • Channel `chrome` (real Google Chrome) first, bundled Chromium as fallback — Chrome's build id trusts more.
  • Network capture: `page.on("request")` records every `/graphql` URL into `self._gql`; open_zone reads the
    seed `SearchResultsPlacements` URL out of it (the ids only exist once a real search fires).
  • NO proxy is ever configured here. This connector is `cost_class: anti-bot` but runs $0 from a residential
    OR datacenter browser; per the standing rule it must NEVER acquire a per-GB proxy "to make it work".
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
