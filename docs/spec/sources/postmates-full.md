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


| column | type | filled |
|---|---|---|
| `item_uuid` | `VARCHAR` | 100.0% |
| `product_uuid` | `VARCHAR` | 74.7% |
| `store_uuid` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `section` | `VARCHAR` | 100.0% |
| `subsection` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 23.4% |
| `gtins` | `VARCHAR` | 23.4% |
| `price` | `DOUBLE` | 100.0% |
| `list_price` | `DOUBLE` | 36.4% |
| `on_promo` | `BOOLEAN` | 100.0% |
| `discount` | `DOUBLE` | 100.0% |
| `promo_text` | `VARCHAR` | 17.3% |
| `promo_tag` | `VARCHAR` | 17.3% |
| `promo_type` | `VARCHAR` | 17.6% |
| `promo_pct` | `DOUBLE` | **4.1%** |
| `promo_flat` | `DOUBLE` | **4.1%** |
| `promo_uuid` | `VARCHAR` | 17.6% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `is_sold_out` | `BOOLEAN` | 100.0% |
| `suspend_reason` | `VARCHAR` | 25.3% |
| `suspend_until` | `VARCHAR` | **0%** ‹never populated› |
| `low_availability` | `VARCHAR` | **0%** ‹never populated› |
| `avail_state` | `VARCHAR` | 74.7% |
| `stock_label` | `VARCHAR` | **2.2%** |
| `max_qty` | `BIGINT` | 100.0% |
| `min_qty` | `DOUBLE` | 100.0% |
| `increment_qty` | `DOUBLE` | 100.0% |
| `default_qty` | `BIGINT` | 100.0% |
| `sold_by` | `VARCHAR` | 100.0% |
| `priced_by` | `VARCHAR` | 100.0% |
| `is_alcohol` | `BOOLEAN` | 100.0% |
| `num_alcoholic` | `BIGINT` | 9.6% |
| `age_rule` | `VARCHAR` | **1.6%** |
| `abv` | `DOUBLE` | 6.8% |
| `pack` | `BIGINT` | 14.8% |
| `item_size` | `VARCHAR` | 72.4% |
| `nutritional_info` | `VARCHAR` | 33.7% |
| `classifications` | `VARCHAR` | **0%** ‹never populated› |
| `dietary_labels` | `VARCHAR` | 21.8% |
| `endorsements` | `VARCHAR` | 35.0% |
| `description` | `VARCHAR` | 25.3% |
| `image` | `VARCHAR` | 100.0% |
| `image_count` | `BIGINT` | 100.0% |
| `zone` | `VARCHAR` | 100.0% |
| `raw_json` | `VARCHAR` | **0%** ‹never populated› |

Fill measured over **full table** (3,190 rows).

> **4 columns never populated:** `suspend_until`, `low_availability`, `classifications`, `raw_json`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


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
