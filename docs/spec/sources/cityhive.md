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


| column | type |
|---|---|
| `store` | `VARCHAR` |
| `base` | `VARCHAR` |
| `platform` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `price_value` | `DOUBLE` |
| `sku` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `size_ml` | `BIGINT` |
| `image` | `VARCHAR` |
| `option_id` | `VARCHAR` |
| `bev_category` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `run_id` | `VARCHAR` |


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
