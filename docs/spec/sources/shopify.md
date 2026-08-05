# Shopify (national sweep) — `shopify`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `shopify` |
| Runs | `import off_premise as m; m.national_sweep('shopify')` |
| Module | `unifyd/off_premise.py` — 1113 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi`, `patchright` |
| Unit test | **none** |


**Registry note.** census sweep's Shopify pass — SHOPIFY_SEED via open /products.json ($0); OFFPREM_SERP=1 adds BD SERP discovery. Replaced standalone shopify_scraper (archived)


## 2. Transport

_No literal endpoint constant in `off_premise.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `bottlecapps`, `brightdata`, `browser_warm`, `cocktail_taxonomy`, `doordash`, `observe`, `raw_capture`, `runlog`, `warehouse`


## 3. What it lands


### `national_shopify_products`

1,141 rows · 16 columns


| column | type |
|---|---|
| `store` | `VARCHAR` |
| `platform` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `price_value` | `DOUBLE` |
| `sku` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `size_opt` | `VARCHAR` |
| `item_code` | `VARCHAR` |
| `bev_category` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `container` | `VARCHAR` |
| `unit_size` | `DOUBLE` |
| `size_uom` | `VARCHAR` |
| `pack_count` | `BIGINT` |
| `total_size` | `DOUBLE` |


## 4. `off_premise.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
off_premise.py — first-party catalog/inventory from a retailer's OWN e-commerce site.

The geo sweep showed small independents have no online store (-> aggregators only), but the LARGE-format
independents + chains run real online catalogs on a handful of platforms. Discovery = Google Maps (the store's
site); extraction = a PLATFORM RECIPE (prove the config once, persist it — see the recipe-store idea). This
starts with BigCommerce (Haskell's), which reuses the proven ABC FWS pattern: /xmlsitemap.php?type=products
enumerates every product, and each product page carries og:title + og:price:amount in the SERVER HTML (no JS).
City Hive / Bottlecapps / Shopify / WooCommerce recipes are the next platforms. Lands <slug>_catalog + dated
retail_observations, so off-premise inventory tracks over time like the DoorDash retail pulls.

    python off_premise.py --store haskells --sample 25      # bounded proof
    python off_premise.py --store haskells                  # full catalog
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
