# VIP Brand Builder (distributor catalogs) — `vip-brandbuilder`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `vip-brandbuilder` |
| Runs | `import vtinfo_bbs as m; m.pull()` |
| Module | `unifyd/vtinfo_bbs.py` — 268 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 8192 MB / 5400 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** products.vtinfo.com/bbs — distributor product+package catalog w/ retail UPCs, no auth. Distributor list = vip_brandbuilder_directory (status='confirmed'), populated by vip-brandbuilder-census over the full 00000-99999 sourceCode keyspace. This is the Overlay's Tier-3 spine: 630k items / 338 books, 97% carrying a retail UPC.


## 2. Transport

| constant | value |
|---|---|
| `BBS` | `https://products.vtinfo.com/bbs/v1/distributor` |


**Depends on** `warehouse`


## 3. What it lands


### `vip_brandbuilder_items`

698,807 rows · 25 columns


| column | type |
|---|---|
| `distributor_id` | `VARCHAR` |
| `distributor_name` | `VARCHAR` |
| `vip_source_id` | `BIGINT` |
| `vip_customer_id` | `BIGINT` |
| `category` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `product_id` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `product_type` | `VARCHAR` |
| `style_name` | `VARCHAR` |
| `style_type` | `VARCHAR` |
| `sub_style_type` | `VARCHAR` |
| `abv` | `VARCHAR` |
| `ibu` | `VARCHAR` |
| `ratebeer_score` | `VARCHAR` |
| `ratebeer_style` | `VARCHAR` |
| `dist_item_code` | `VARCHAR` |
| `retail_upc` | `VARCHAR` |
| `retail_upc_raw` | `VARCHAR` |
| `package_name` | `VARCHAR` |
| `beverage_id` | `VARCHAR` |
| `image` | `VARCHAR` |
| `sell_sheet` | `VARCHAR` |
| `description` | `VARCHAR` |
| `pulled_at` | `VARCHAR` |


**Written by** `vtinfo_bbs.py:234` (write_accumulate)


## 4. `vtinfo_bbs.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
vtinfo_bbs.py — VIP **Brand Builder** distributor catalog (products.vtinfo.com/bbs).

WHY THIS IS A PLATFORM RECIPE (not a one-off scrape):
  Vermont Information Processing (VIP) hosts a "Brand Builder" product-portfolio app for a huge
  share of US beverage-alcohol DISTRIBUTORS. Every distributor's portfolio lives at the same
  place, keyed only by that distributor's VIP **sourceCode** (a short id like `01191`):

      https://products.vtinfo.com/brandbuilder/<sourceCode>/brands/tab/1   (the Angular UI)
      https://products.vtinfo.com/bbs/v1/distributor/<sourceCode>/{info,brands,products,styles}

  The UI is a single-page app, but it reads a plain, **open JSON API** (the `bbs/v1` service):
  no auth, no cookie, no token — `access-control-allow-origin: *`. So proving ONE distributor
  proves the platform: point this at any sourceCode and you get that distributor's whole book —
  every brand, product, package, and **retail UPC** — as a deterministic pull. That's the
  "prove the system once → every store on it is free" recipe payoff ([[system-scrape-recipes]]),
  and it's the fastest path toward catalog coverage of the major distributors (Columbia, Reyes,
  Breakthru, RNDC, …) one sourceCode at a time.

THE THREE ENDPOINTS WE READ (all GET, all `{code, data, success}` envelopes):
  • /info     → distributor identity (name, vipSourceId, vipCustomerId, logo).
  • /brands   → brand groups → brands[] (name, description, website, socials). We use the group
                names (Domestic/Craft/Import Beer, Cider, Spirits, Wineries, …) to tag each item's
                CATEGORY via brand_name == product_supplier_name (~98% hit on Columbia).
  • /products → the master: products[] each with abv/ibu/style/supplier/ratebeer + product_packages[],
                and each package carries `dist_item_code` (the distributor's item #) + `retail_upc`.

GRAIN: one row per **package** (product × pack size) — the master-item grain ([[master-item-grain]]),
  denormalized with product + distributor context. Identity = distributor_id | dist_item_code
  (always present on Columbia: 6756/6756). UPCs are zero-padded to 12 the way the app itself does.

SELF-HEALING: if /products lands products but yields 0 package rows, or retail_upc fill collapses,
  the run is marked `degraded` with warnings[] (the package selectors drifted) rather than emitting
  bad data — same honesty contract as the other owned scrapers.

CLI:  python vtinfo_bbs.py --dist 01191
      python vtinfo_bbs.py --discover https://a-distributor.com/brands   # find its sourceCode
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
