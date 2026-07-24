# ABC Fine Wine & Spirits — Scraper Handoff

> Polite, directional **per-store inventory tracker** on BigCommerce. The store is a product option,
> so per-store in/out + chain price come from the allowed product page; a **real bottle count** per
> store comes from the sanctioned storefront GraphQL API. This is the **gold-standard Tier-1 model**:
> free, direct, exact units, $0.

| | |
|---|---|
| **Status** | Live |
| **Registry id** | `abc-fws` (inventory); `abc-facets` = SearchSpring taxonomy+UPC; `abc-catalog` = BigCommerce sitemap |
| **Entrypoint** | `import abc_fws_scraper as m; m.pull(crawl_all=True)` |
| **Class / cadence** | `headless` (stdlib) / daily |
| **Lands to** | `retail_observations` (+ `abc_products` via the facets pull) |
| **Inventory signal** | Exact per-store on-hand (`availableToSell`) |
| **Key files** | `abc_fws_scraper.py` (inventory), `abc_facets.py` (SearchSpring taxonomy+UPC), `abc_catalog.py` |

## What we can accomplish

Daily **per-store price + exact on-hand** across the whole ABC chain (~2,100 products × ~133 stores),
plus day-over-day directional signal: price moves, OOS↔restock transitions, assortment churn. It's
the reference implementation for a free, polite, self-healing Counts source. A companion SearchSpring
pull (`abc_facets.py`) adds the **drill-path taxonomy** (authoritative varietal/type/region) and the
**UPC** the inventory path lacks.

## Access & mechanism

BigCommerce storefront (`abcfws.com`). The store is a **product option** — each product page lists
~133 store options and `available_variant_values` names the in-stock ones, so per-store in/out + the
chain price come straight from the allowed product page (no robots-disallowed AJAX). For a **real
unit count**, the sanctioned storefront GraphQL API (`/graphql`, Bearer = the JWT embedded in every
product page) exposes each store as a variant carrying `inventory.aggregated.availableToSell`. Polite:
robots 10s crawl-delay, product pages only, honest UA, stdlib-only. Concurrent workers + jitter for speed.

## Levels of pull

1. **Catalog (sitemap)**
   `GET /xmlsitemap.php?type=products&page=N`
   The whole catalog as (sku, product URL) — every product type, no category filter.
2. **Product page (HTML)**
   `GET <product URL>`
   Per-store **binary in/out** (store = BigCommerce option; in-stock subset = `available_variant_values`) + the chain-level price. The fallback path.
3. **Storefront GraphQL (real qty)**
   `POST /graphql  (Bearer = JWT from the page) → variants[].inventory.aggregated.availableToSell`
   The **actual bottle count per store** + variant sku/upc/gtin/isInStock/price. The preferred path — the number the HTML omits.

## Every field we capture


**Per-store cell (snapshot keyed `sku|store`; lands to `retail_observations`)**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `sku` | str | BigCommerce product id (trailing id in the product URL). |
| `store` | str | Store label — a BigCommerce product option (“ABC #016 - Winter Garden” / “Online”). |
| `price` | float $ | Chain-level price (one BigCommerce price across stores). |
| `instock` | bool | Per-store in/out. From `available_variant_values` (HTML) or `inventory.isInStock` (GraphQL). |
| `qty` | int | **Real per-store units on hand** via the storefront GraphQL `inventory.aggregated.availableToSell`. None when only the HTML in/out fallback was available. |
| `upc / gtin` | str | variant.upc / variant.gtin (GraphQL path). Often empty — ABC BigCommerce variants carry no barcode; UPCs come from `abc_products` raw_json (the SearchSpring facet pull) joined on `sku`. |

## Sample record

> **REAL** — pulled from a live snapshot. SKU 365270 at Winter Garden. The **same SKU across the chain that run** shows real per-store on-hand: Online **75**, Winter Garden **25**, Satellite Beach **21**, W. Kennedy **26**, Ormond Beach **20** in stock; several stores **0/out** — all at the same $6.99 chain price.

```json
{
  "sku": "365270",
  "store": "ABC #016 - Winter Garden",
  "price": 6.99,
  "instock": true,
  "qty": 25,
  "upc": "",
  "gtin": ""
}
```

## Gotchas & hard-won learnings

- **ABC is the Tier-1 gold standard** — free, direct, exact units, $0. The storefront GraphQL `availableToSell` is the sanctioned API, NOT the robots-disallowed legacy stock AJAX. Model other chains on it.
- **BigCommerce variants carry NO upc/gtin at the source.** UPCs come from `abc_products` (the SearchSpring facet pull, `abc_facets.py`) joined on `sku`. Keep both pulls; they share the `sku`.
- **Volume-triggered 403 wall mid-crawl.** The full daily sweep periodically hits a WAF 403 partway through (single probes still 200 → it's rate-based, not a UA/robots block). The scraper auto-flips to the BD proxy on `Blocked` for the remainder (`polite.reset(host)` clears the breaker for the new transport). Starts on `ABC_PROXY`.
- **Store-label double-prefix bug** (“ABC #ABC #003…”) — fixed at capture, healed at read. Per the normalization rule, landed data is never rewritten; the fix is a read-time translation.
- **Self-reports `degraded`** if the store-option / `available_variant_values` selectors parse on <50% of pages (markup drift). Validated live at ~13.9k products via sitemap; the inventory drill-in verified 132/142 stores with stock.
- **SearchSpring deep-paging caps at 10,000 results** (page 101 → HTTP 400). The facet pull gets ~10k of ~14k; the last ~4k need a **category-split** (harvest per top category, each <10k).

## Maintenance & health

Directional by design — a snapshot keyed `sku|store` diffs against the prior run for price moves /
OOS↔restock / new+dropped SKUs. The `qty` fill-rate distinguishes the GraphQL path (has counts) from
the HTML fallback (in/out only). Watch the degraded flag and the mid-run 403→proxy flip in the logs.

## Files & entrypoints

- `unifyd/abc_fws_scraper.py` — `harvest_ids()`, `parse_stores()` (HTML in/out), `graphql_stores()` (real qty), `diff_snapshots()`; the `diff_snapshots` helper is reused by Spec's and Instacart.
- `unifyd/abc_facets.py` — SearchSpring (siteId `p16j4k`) drill-path taxonomy + UPC → `abc_products` + `source_taxonomy`.
- Run: `python abc_fws_scraper.py --all` (full crawl)  ·  `--sample 40` (deterministic spread).


---
*Part of the Hoodie Suite scraper cutover pack. See [`README.md`](README.md) for the shared architecture, anti-bot infrastructure, and standing rules that apply to every source.*
