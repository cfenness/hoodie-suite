# Target — `target`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `target` |
| Runs | `import target_scraper as m; m.run()` |
| Module | `unifyd/target_scraper.py` — 405 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | bd |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi`, `patchright` |
| Unit test | **none** |


**Registry note.** RedSky API


## 2. Transport

| constant | value |
|---|---|
| `REDSKY` | `https://redsky.target.com/redsky_aggregations/v1/web` |


**Depends on** `browser_warm`, `observe`, `resi`, `warehouse`


## 3. What it lands


### `target_products`

1,584 rows · 9 columns


| column | type |
|---|---|
| `tcin` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `price` | `DOUBLE` |
| `promo` | `INTEGER` |
| `image_url` | `VARCHAR` |
| `category` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `source` | `VARCHAR` |


**Written by** `target_scraper.py:319` (write_accumulate), `target_scraper.py:274` (write_accumulate)


### `target_stores`

1,189 rows · 9 columns


| column | type |
|---|---|
| `store_id` | `VARCHAR` |
| `name` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `zip` | `VARCHAR` |
| `address` | `VARCHAR` |
| `phone` | `VARCHAR` |
| `lat` | `INTEGER` |
| `lon` | `INTEGER` |


**Written by** `target_scraper.py:188` (write_accumulate)


## 4. `target_scraper.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
target_scraper.py — Target bev-alc via the RedSky API, through the ban-safe Bright Data Unlocker.

Target has no login/age gate on product data, a clean JSON API, and — unusually — a REAL numeric per-store
inventory count. Two RedSky calls per term:
  • plp_search_v2                     -> products: tcin, name, price, brand, image (discovery + price)
  • product_summary_with_fulfillment  -> per-store location_available_to_promise_quantity (numeric inventory)
TWO transports (auto-selected): when an ISP pool is configured (ISP_PROXIES), a warmed local-Chrome session
through a US ISP IP does an in-page fetch of RedSky — FLAT-RATE, no BD (a warmed cookie won't transfer to curl,
so the browser stays in the loop; slower than BD but $0 marginal). Otherwise the BD Unlocker (POST
api.brightdata.com/request) — metered, faster. Lands target_products + retail_observations, with image +
is_hemp. Store ids from the store-locator; a few markets to start.

    python target_scraper.py                      # default terms x stores
    python target_scraper.py --terms "wine,beer"  --stores "2259:20001:DC"
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
