# Haskell's (MN) — `haskells`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `haskells` |
| Runs | `import haskells as m; m.run(limit=None)` |
| Module | `unifyd/haskells.py` — 215 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / 10800 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** first-party site; full-catalog crawl outgrew the 5400s default (timed out 07-18)


## 2. Transport

| constant | value |
|---|---|
| `BASE` | `https://haskells.com` |


**Depends on** `hemp_scan`, `observe`, `polite`, `warehouse`


## 3. What it lands


### `haskells_products`

10,535 rows · 19 columns


| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `sku` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | **0%** ‹never populated› |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | **2.9%** |
| `category` | `VARCHAR` | **0%** ‹never populated› |
| `price` | `DOUBLE` | 100.0% |
| `retail_price` | `DOUBLE` | 100.0% |
| `on_sale` | `BOOLEAN` | 100.0% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `qty` | `BIGINT` | 100.0% |
| `url` | `VARCHAR` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `hemp_signal` | `VARCHAR` | **1.4%** |
| `image` | `VARCHAR` | 73.8% |
| `captured_at` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `raw_json` | `VARCHAR` | 100.0% |

Fill measured over **full table** (10,535 rows).

> **2 columns never populated:** `upc`, `category`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `haskells.py:167` (write_accumulate)


## 4. `haskells.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
haskells.py — Haskell's Wine & Spirits (haskells.com, Minneapolis MN) full catalog + REAL inventory counts.

Haskell's runs on **BigCommerce** (same platform as ABC FWS), so it reuses that recipe: the product sitemap
is the whole catalog, and the BigCommerce **Storefront GraphQL API** — authorized by the public JWT embedded
in every product page — returns `inventory.aggregated.availableToSell`, the true on-hand unit count (not just
in/out). It's a single store (unlike ABC's per-store options), so one quantity per product.

We grab the WHOLE catalog (bev-alc first) and FLAG hemp/THC (Haskell's has a live `/thc/` category), so this
one source feeds both the bev-alc master and the hemp inventory view. This is a data vendor's Haskell's feed
done ourselves, with the count they can't reliably get (their extraction was pinned at 100 by pagination) —
the sitemap sidesteps pagination entirely.

Lands `haskells_products` (latest snapshot + raw) + the dated observe.record time-series (qty = availableToSell).
Polite (rate-limit/backoff/breaker via polite.py), stdlib + the shared warehouse/observe/hemp_scan layer.

    python haskells.py --limit 40      # smoke test (first 40 products)
    python haskells.py                 # full catalog -> haskells_products
```


## 5. Raw source fields

Endpoint: `POST /graphql  site.route(path).node ...Product` · grain: product (single store)


| raw field | meaning | maps to |
|---|---|---|
| `entityId` | BigCommerce product id | `product_id` |
| `name` | product name | `name` |
| `sku` | SKU | `sku` |
| `brand.name` | brand | `brand` |
| `prices.price.value` | current price | `price` |
| `prices.retailPrice.value` | list/retail price | `retail_price` |
| `prices.salePrice.value` | sale price (if on sale) | `price + on_sale` |
| `variants[].sku` | variant SKU | `sku` |
| `variants[].gtin` | UPC/GTIN | `upc` |
| `variants[].upc` | UPC (alt field) | `upc (fallback)` |
| `variants[].inventory.isInStock` | in-stock bool | `in_stock` |
| `variants[].inventory.aggregated.availableToSell` | EXACT on-hand units | `qty` |
| `defaultImage.url` | image | `image` |


Whole catalog via sitemap (sidesteps the pagination-100 cap). Storefront JWT scraped from the product page. Category from the page breadcrumb (not GraphQL here).
