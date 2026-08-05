# Kroger (API UPC seed) — OFF: no inventory — `kroger-api`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `kroger-api` |
| Runs | `import kroger_api as m; m.main()` |
| Module | `unifyd/kroger_api.py` — 300 lines |
| Cadence | weekly |
| Enabled | no — does not run on a cadence |
| Executor class | `creds` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | `KROGER_CLIENT_ID`, `KROGER_CLIENT_SECRET` |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** OFF BY CHOICE — public OAuth API has NO inventory. Inventory comes from kroger (atlas). Do not enable to clear a no-creds warning.


## 2. Transport

| constant | value |
|---|---|
| `TOKEN_URL` | `https://api.kroger.com/v1/connect/oauth2/token` |
| `API` | `https://api.kroger.com/v1` |


**Depends on** `observe`, `runlog`, `warehouse`


## 3. What it lands


### `kroger_products`

36,812 rows · 20 columns


| column | type |
|---|---|
| `product_id` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `category` | `VARCHAR` |
| `size` | `VARCHAR` |
| `price` | `DOUBLE` |
| `promo` | `DOUBLE` |
| `on_promo` | `BOOLEAN` |
| `stock_level` | `VARCHAR` |
| `in_stock` | `BOOLEAN` |
| `image_url` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `raw_json` | `VARCHAR` |
| `location_id` | `VARCHAR` |
| `term` | `VARCHAR` |
| `run_id` | `VARCHAR` |
| `store` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |


**Written by** `kroger_api.py:218` (write_parquet)


## 4. `kroger_api.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
kroger_api.py — Kroger bev-alc price + inventory via the OFFICIAL Kroger Developer API (connId kroger).

Unlike the retailers we scrape, Kroger publishes a real API: OAuth2 client-credentials → Products
(brand, size, UPC, regular/promo price, and STORE-LEVEL stock level when a locationId is passed) +
Locations. That gives genuine per-store inventory — the store-level "what's in stock" the plan calls for.
Lands `kroger_products` + `kroger_runs` in the warehouse. Runs anywhere (Mac or the cloud runner).

Setup: create an app at https://developer.kroger.com (scope: product.compact), then provide the creds
either as env vars (KROGER_CLIENT_ID / KROGER_CLIENT_SECRET — the cloud runner passes these from repo
secrets) or in warehouse.env. Cred-gated: no-op with a note when they're absent.

    python kroger_api.py                      # default bev-alc terms across a few store zips
    python kroger_api.py --zips 30303,10001 --terms "bourbon,vodka,cabernet"
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
