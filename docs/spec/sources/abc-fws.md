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

60,510,145 rows · 19 columns · 4,327 partitions · **2 different schemas in a 6-partition sample — this table has drifted**


| column | type | filled |
|---|---|---|
| `date` | `VARCHAR` | 100.0% |
| `observed_at` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `chain` | `VARCHAR` | **0%** ‹never populated› |
| `store` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | **0%** ‹never populated› |
| `gtin` | `VARCHAR` | **0%** ‹never populated› |
| `brand` | `VARCHAR` | 80.7% |
| `name` | `VARCHAR` | 100.0% |
| `price` | `DOUBLE` | 99.7% |
| `promo` | `DOUBLE` | **0%** ‹never populated› |
| `promo_text` | `VARCHAR` | **0%** ‹never populated› |
| `on_promo` | `BOOLEAN` | 100.0% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `qty` | `DOUBLE` | 81.1% |
| `stock_level` | `VARCHAR` | **0.2%** |
| `is_hemp` | `BOOLEAN` | 100.0% |

Fill measured over **newest 40 of 4327 partitions** (1,756,522 rows).

> **5 columns never populated:** `chain`, `upc`, `gtin`, `promo`, `promo_text`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `observe.py:156` (write_partition)


### `abc_catalog`

14,098 rows · 7 columns


| column | type | filled |
|---|---|---|
| `sku` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | **0%** ‹never populated› |
| `size` | `VARCHAR` | 70.2% |
| `upc` | `VARCHAR` | 20.1% |
| `price` | `DOUBLE` | 100.0% |
| `url` | `VARCHAR` | 100.0% |

Fill measured over **full table** (14,098 rows).

> **1 column never populated:** `brand`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


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
