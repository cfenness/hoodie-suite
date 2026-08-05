# Postmates — bounded full-detail crawl — `postmates-full`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `postmates-full` |
| Runs | `import os; os.environ['RESI_ISP_ONLY']='1'; import ue_crawl as m; m.main(['--zones','New York, NY;Los Angeles, CA;Chicago, IL;Miami, FL;Houston, TX','--site','postmates','--max-stores','60','--max-items-enrich','40'])` |
| Module | `unifyd/ue_crawl.py` — 577 lines |
| Cadence | daily |
| Enabled | no — does not run on a cadence |
| Executor class | `mac` |
| Cost class | free |
| Memory / timeout | 8192 MB / 10800 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** Postmates twin of ubereats-full — same bounds, same $0/no-proxy posture, manual trigger only.


## 2. Transport

| constant | value |
|---|---|
| `API` | `https://www.ubereats.com/_p/api/` |


**Depends on** `browser_warm`, `resi`, `ubereats`, `warehouse`


## 3. What it lands


### `postmates_products`

3,190 rows · 47 columns


| column | type |
|---|---|
| `item_uuid` | `VARCHAR` |
| `product_uuid` | `VARCHAR` |
| `store_uuid` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `name` | `VARCHAR` |
| `section` | `VARCHAR` |
| `subsection` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `gtins` | `VARCHAR` |
| `price` | `DOUBLE` |
| `list_price` | `DOUBLE` |
| `on_promo` | `BOOLEAN` |
| `discount` | `DOUBLE` |
| `promo_text` | `VARCHAR` |
| `promo_tag` | `VARCHAR` |
| `promo_type` | `VARCHAR` |
| `promo_pct` | `DOUBLE` |
| `promo_flat` | `DOUBLE` |
| `promo_uuid` | `VARCHAR` |
| `in_stock` | `BOOLEAN` |
| `is_sold_out` | `BOOLEAN` |
| `suspend_reason` | `VARCHAR` |
| `suspend_until` | `VARCHAR` |
| `low_availability` | `VARCHAR` |
| `avail_state` | `VARCHAR` |
| `stock_label` | `VARCHAR` |
| `max_qty` | `BIGINT` |
| `min_qty` | `DOUBLE` |
| `increment_qty` | `DOUBLE` |
| `default_qty` | `BIGINT` |
| `sold_by` | `VARCHAR` |
| `priced_by` | `VARCHAR` |
| `is_alcohol` | `BOOLEAN` |
| `num_alcoholic` | `BIGINT` |
| `age_rule` | `VARCHAR` |
| `abv` | `DOUBLE` |
| `pack` | `BIGINT` |
| `item_size` | `VARCHAR` |
| `nutritional_info` | `VARCHAR` |
| `classifications` | `VARCHAR` |
| `dietary_labels` | `VARCHAR` |
| `endorsements` | `VARCHAR` |
| `description` | `VARCHAR` |
| `image` | `VARCHAR` |
| `image_count` | `BIGINT` |
| `zone` | `VARCHAR` |
| `raw_json` | `VARCHAR` |


## 4. `ue_crawl.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
ue_crawl.py — NATIONAL UberEats crawler (bev-alc, on + off premise). Full capture from getStoreV1 (catalog) +
getMenuItemV1 (UPC / price / promo / recipe-customizations), across the whole US.

Architecture (every step proven — see the ue_probe*.py investigation):
  1. GEOCODE headless (mapsSearchV1 + getDeliveryLocationV1 via curl_cffi + residential proxy): any US
     city/ZIP/address -> an Uber location (reference + coords). No auth, no browser.
  2. Build a pl= feed URL from that location.
  3. ONE warmed real-Chrome session (through the residential proxy) navigates each zone's pl= URL -> the session
     is now located there -> getFeedV1 returns that zone's merchants.
  4. Per store: getStoreV1 (catalog) + getMenuItemV1 (full per-item detail incl. UPC + on-prem recipe). These
     replay in-session via the browser's own fetch — the reCAPTCHA/PX context is inherited.
  5. Land FULL capture to <site>_products (dedup by store_uuid|item_uuid) — resumable across overlapping zones.

Catalogs are ZONE-BOUND (a store's getStoreV1 only returns data when the session is in its area), so coverage =
enumerating US zones. Stores are deduped by uuid so overlapping zones don't re-crawl.

  python ue_crawl.py --zones-file zones_us.txt --max-stores 300         # all listed zones
  python ue_crawl.py --zones "Chicago, IL;Miami, FL" --max-stores 50    # ad-hoc
  python ue_crawl.py --site postmates ...
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
