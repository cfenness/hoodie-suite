# Uber Eats — bounded full-detail crawl — `ubereats-full`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `ubereats-full` |
| Runs | `import os; os.environ['RESI_ISP_ONLY']='1'; import ue_crawl as m; m.main(['--zones','New York, NY;Los Angeles, CA;Chicago, IL;Miami, FL;Houston, TX','--site','ubereats','--max-stores','60','--max-items-enrich','40'])` |
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


**Registry note.** ONE bounded run (5 metros, capped stores/items), NO proxy (RESI_ISP_ONLY=1 forbids metered spend) — validates the bare Fly IP before any wider run. Manual trigger only.


## 2. Transport

| constant | value |
|---|---|
| `API` | `https://www.ubereats.com/_p/api/` |


**Depends on** `browser_warm`, `resi`, `ubereats`, `warehouse`


## 3. What it lands


### `ubereats_products`

2,160,806 rows · 16 columns


| column | type |
|---|---|
| `store_uuid` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `source` | `INTEGER` |
| `item_uuid` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `INTEGER` |
| `upc` | `VARCHAR` |
| `gtin` | `INTEGER` |
| `price` | `DOUBLE` |
| `list_price` | `DOUBLE` |
| `promo` | `INTEGER` |
| `size` | `INTEGER` |
| `abv` | `DOUBLE` |
| `in_stock` | `BOOLEAN` |
| `stock_label` | `VARCHAR` |
| `category` | `INTEGER` |


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
