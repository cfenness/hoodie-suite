# ABC FW&S (inventory) — `abc-fws`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `abc-fws` |
| Runs | `import abc_fws_scraper as m; m.pull(crawl_all=True)` |
| Module | `unifyd/abc_fws_scraper.py` — 685 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | proxy |
| Memory / timeout | 8192 MB / 21600 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** per-store inventory → lands retail_observations (NOT abc_products, which abc-facets owns/overwrites)


## 2. Transport

| constant | value |
|---|---|
| `BASE` | `https://abcfws.com` |


**Depends on** `observe`, `polite`, `raw_capture`, `warehouse`


## 3. What it lands


### `retail_observations`

59,077,605 rows · 19 columns · 4,296 partitions · **3 different schemas in a 6-partition sample — this table has drifted**


| column | type |
|---|---|
| `date` | `VARCHAR` |
| `observed_at` | `BIGINT` |
| `source` | `VARCHAR` |
| `chain` | `VARCHAR` |
| `store` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `gtin` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `name` | `VARCHAR` |
| `price` | `DOUBLE` |
| `promo` | `DOUBLE` |
| `promo_text` | `VARCHAR` |
| `on_promo` | `BOOLEAN` |
| `in_stock` | `BOOLEAN` |
| `qty` | `DOUBLE` |
| `stock_level` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |


**Written by** `observe.py:156` (write_partition)


### `abc_catalog`

14,098 rows · 7 columns


| column | type |
|---|---|
| `sku` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `size` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `price` | `DOUBLE` |
| `url` | `VARCHAR` |


**Written by** `abc_catalog.py:77` (write_accumulate), `abc_catalog.py:68` (write_accumulate), `abc_fws_scraper.py:435` (write_accumulate)


## 4. `abc_fws_scraper.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
abc_fws_scraper.py — polite, directional inventory tracker for ABC Fine Wine & Spirits.

Why this exists
---------------
ABC FWS (abcfws.com) runs on BigCommerce. The public storefront exposes, per SKU:
  • a price (in the server HTML), and
  • a binary in-stock / out-of-stock status.
It does NOT expose a numeric quantity-on-hand, and per-store stock is behind an AJAX
endpoint that robots.txt disallows. So you cannot literally compute "units sold =
yesterday's qty − today's qty". What you CAN observe, day over day, is *directional*:
  • price changes,
  • out-of-stock ↔ restock transitions,
  • assortment churn — SKUs appearing/disappearing from the catalog.
Polled on a cadence, that's an imprecise-but-useful read on what's moving.

How it stays polite
-------------------
robots.txt gives our crawler class a 10s crawl-delay and disallows cart/checkout/
account/admin/search/facet + the per-store stock AJAX. This scraper touches ONLY the
product sitemap and product pages (both allowed), sleeps ABC_DELAY (default 10s) between
requests, sends an honest identifying User-Agent, and caps how many pages it pulls per
run. It is read-only and stdlib-only (urllib + regex — no new dependencies).

Cadence detection
-----------------
The sitemap carries no <lastmod>, so cadence is inferred from the data itself: each run
snapshots {price, in-stock, ETag/Last-Modified} for a deterministic SAMPLE of SKUs and
diffs against the previous snapshot. Over a few daily runs, when those values flip tells
you how often the catalog refreshes — without crawling all ~2,100 products every time.

CLI:
    python abc_fws_scraper.py --sample 40 --out ./abc_out
    python abc_fws_scraper.py --all --limit 500       # wider crawl (slow; respects delay)
```


## 5. Raw source fields

Endpoint: `POST /graphql (variants+inventory) + product page (store options) — public storefront JWT` · grain: product × store (store = BigCommerce option value)


| raw field | meaning | maps to |
|---|---|---|
| `variants[].inventory.aggregated.availableToSell` | EXACT per-store on-hand (=available_on_hand=stock) | `qty` |
| `variants[].sku` | SKU (sku-storeValue) | `sku` |
| `variants[].upc` | UPC — was NOT requested; added to the query (the master key) | `upc` |
| `variants[].gtin` | GTIN — added to the query | `gtin` |
| `variants[].inventory.isInStock` | in-stock bool | `instock` |
| `variants[].options...values.label` | the STORE ('ABC #003 - OBT' / 'Online') | `store` |
| `prices.price.value` | chain price (one price, not per-store) | `price` |
| `available_variant_values[] (product page)` | in-stock store-option values (HTML fallback path) | `instock set` |
| `available_on_hand / available_to_sell / stock` | same number via the availability endpoint | `qty (via GraphQL)` |
| `out_of_stock_behavior / out_of_stock_message` | OOS handling | `raw` |
| `v3_variant_id / variantId / mpn / weight` | BigCommerce ids | `raw` |


abc_fws_scraper.py. Counts (availableToSell) + now UPC/GTIN. Store is a BC product option; the product page's available_variant_values is the robots-safe in/out fallback when GraphQL is off.
