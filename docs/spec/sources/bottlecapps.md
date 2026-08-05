# Bottlecapps network — `bottlecapps`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `bottlecapps` |
| Runs | `import bottlecapps as m; m.national()` |
| Module | `unifyd/bottlecapps.py` — 298 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `mac` |
| Cost class | mac |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi`, `patchright` |
| Unit test | **none** |


**Registry note.** DataDome — patchright


## 2. Transport

_No literal endpoint constant in `bottlecapps.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `browser_warm`, `observe`, `off_premise`, `warehouse`


## 3. What it lands


### `bottlecapps_products`

227 rows · 19 columns


| column | type |
|---|---|
| `store` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `pid` | `VARCHAR` |
| `url` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `sku` | `VARCHAR` |
| `gtin` | `VARCHAR` |
| `price` | `DOUBLE` |
| `currency` | `VARCHAR` |
| `size` | `VARCHAR` |
| `availability` | `VARCHAR` |
| `description` | `VARCHAR` |
| `image` | `VARCHAR` |
| `rating` | `VARCHAR` |
| `rating_count` | `BIGINT` |
| `captured_at` | `BIGINT` |
| `raw_json` | `VARCHAR` |


**Written by** `bottlecapps.py:197` (write_accumulate)


## 4. `bottlecapps.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
bottlecapps.py — FULL-CAPTURE Bottlecapps (liquorapps) scraper.

Bottlecapps powers hundreds of independent liquor retailers' e-commerce — DataDome-protected JS storefronts where
everything is keyed by store_id (category = /s-<sid>/c-N/buy-<slug>, product = /product/s-<sid>/p-<pid>/buy-<slug>).

NO Bright Data: our OWN Chrome via browser_warm + patchright clears DataDome once (the CDP-automation leak DataDome
scores on is patched), then IN-PAGE FETCH reuses that trusted session to pull the server-rendered HTML. Per store:
enumerate every category → union all product URLs → each product page yields FULL structured data from its JSON-LD
`Product` block + the `<input name="upc">` hidden field: name, brand, UPC, sku, price, size, availability,
description, rating, image. Everything is captured, incl. raw_json (the source fields), per the full-capture directive.

Supersedes off_premise.bottlecapps_catalog — which was a shallow sample (max 10 categories, 8 scrolls, ~60 products/
store, NO price, NO UPC, on Bright Data). Discovery: SERP the platform fingerprint → store domains (find_stores).
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
