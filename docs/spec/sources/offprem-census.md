# Off-premise census (Shopify/Woo/Wix/Sqsp) — `offprem-census`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `offprem-census` |
| Runs | `import off_premise as m, warehouse, re;markets=sorted(set(re.sub(r'_offprem_census$','',d['name']) for d in warehouse.list_datasets() if d['name'].endswith('_offprem_census')));[m.run_census(market=x, platforms=('Shopify','WooCommerce','Wix','Squarespace')) for x in markets]` |
| Module | `unifyd/off_premise.py` — 1113 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | proxy |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi`, `patchright` |
| Unit test | **none** |


**Registry note.** 22 markets, no-BD


## 2. Transport

_No literal endpoint constant in `off_premise.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `bottlecapps`, `brightdata`, `browser_warm`, `cocktail_taxonomy`, `doordash`, `observe`, `raw_capture`, `runlog`, `warehouse`


## 3. What it lands


### `offprem_products`

516,629 rows · 36 columns


| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `base` | `VARCHAR` | 100.0% |
| `platform` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 80.9% |
| `price_value` | `DOUBLE` | 100.0% |
| `sku` | `VARCHAR` | 63.5% |
| `upc` | `VARCHAR` | 31.7% |
| `size_ml` | `BIGINT` | 6.5% |
| `bev_category` | `VARCHAR` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |
| `container` | `VARCHAR` | **4.7%** |
| `unit_size` | `DOUBLE` | 6.6% |
| `size_uom` | `VARCHAR` | 6.6% |
| `pack_count` | `BIGINT` | 100.0% |
| `total_size` | `DOUBLE` | 6.6% |
| `tags` | `VARCHAR` | 65.3% |
| `description` | `VARCHAR` | 70.0% |
| `item_code` | `VARCHAR` | 95.5% |
| `product_type` | `VARCHAR` | 70.0% |
| `compare_at_price` | `DOUBLE` | 17.7% |
| `grams` | `BIGINT` | 80.3% |
| `in_stock` | `BOOLEAN` | 89.5% |
| `image` | `VARCHAR` | 75.8% |
| `size_opt` | `VARCHAR` | 15.2% |
| `vintage_opt` | `VARCHAR` | **0.7%** |
| `abv` | `VARCHAR` | **0%** ‹never populated› |
| `vintage` | `VARCHAR` | **0%** ‹never populated› |
| `origin` | `VARCHAR` | **0%** ‹never populated› |
| `bottled_in` | `INTEGER` | **0%** ‹never populated› |
| `region` | `VARCHAR` | **0%** ‹never populated› |
| `sub_region` | `INTEGER` | **0%** ‹never populated› |
| `appellation` | `INTEGER` | **0%** ‹never populated› |
| `varietal` | `VARCHAR` | **0%** ‹never populated› |
| `raw_json` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

> **8 columns never populated:** `abv`, `vintage`, `origin`, `bottled_in`, `region`, `sub_region`, `appellation`, `varietal`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `off_premise.py:976` (write_accumulate)


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
