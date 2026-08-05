# City Hive network — `cityhive`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `cityhive` |
| Runs | `import cityhive as m; m.national(max_stores=100)` |
| Module | `unifyd/cityhive.py` — 222 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `mac` |
| Cost class | mac |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi`, `patchright` |
| Unit test | **none** |


**Registry note.** Cloudflare — patchright


## 2. Transport

_No literal endpoint constant in `cityhive.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `browser_warm`, `observe`, `off_premise`, `warehouse`


## 3. What it lands


### `cityhive_products`

799 rows · 14 columns


| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `base` | `VARCHAR` | 100.0% |
| `platform` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | **0%** ‹never populated› |
| `price_value` | `DOUBLE` | 89.1% |
| `sku` | `VARCHAR` | 89.1% |
| `upc` | `VARCHAR` | **0%** ‹never populated› |
| `size_ml` | `BIGINT` | 84.6% |
| `image` | `VARCHAR` | 93.1% |
| `option_id` | `VARCHAR` | 100.0% |
| `bev_category` | `VARCHAR` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |

Fill measured over **full table** (799 rows).

> **2 columns never populated:** `brand`, `upc`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `cityhive.py:147` (write_accumulate)


## 4. `cityhive.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
cityhive.py — FULL-CAPTURE City Hive scraper, NO Bright Data.

City Hive is the biggest independent-liquor e-commerce platform (~2000 retailers). Its stores are Cloudflare-gated,
so the SEO-surface recipe (off_premise.cityhive_catalog) only works through Bright Data. This module does it BD-free
with our OWN Chrome via browser_warm + patchright: patchright clears the Cloudflare challenge once, then IN-PAGE
FETCH reuses that trusted session to pull the server-rendered pages. Per store: fetch the product sitemap → every
product URL → each product page yields the full record from its JSON-LD Product + OpenGraph (name/brand/price/size/
upc/description/image), via off_premise's proven parsers. Lands to cityhive_products (accumulate, key store|url).
```


## 5. Raw source fields

Endpoint: `GET product page — JSON-LD Product + OpenGraph meta` · grain: product × store


| raw field | meaning | maps to |
|---|---|---|
| `ld:name` | product name (JSON-LD) | `name` |
| `ld:brand.name` | brand | `brand` |
| `ld:sku` | SKU | `sku` |
| `ld:gtin13\|gtin12\|gtin` | UPC/GTIN | `upc` |
| `ld:category` | category | `category` |
| `ld:description` | description | `description` |
| `ld:offers.price` | price (JSON-LD) | `price (fallback)` |
| `ld:image` | image | `image` |
| `og:title` | 'Name SIZE - Store' | `name/size_ml` |
| `product:price:amount` | price (OpenGraph) | `price` |
| `og:description` | 'Buy NAME size for $X from City - Store #NN in City, ST' | `store/store_loc/size` |
| `ch:product:id` | City Hive product id | `pid/sku (fallback)` |
| `og:image` | image | `image (fallback)` |
| `url:option-id` | option id (from product URL) | `option_id` |


Full catalog + price from SEO, no browser. Per-store count needs the session-walled widget API (see [[cityhive-crack]]). prices.json/offers.json are public but need store/option context.
