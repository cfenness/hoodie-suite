# Uber Eats store catalog (sharded) — `ubereats`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `ubereats` |
| Runs | `import os; os.environ['LADDER_MAX_RUNG']='impersonate'; import ue_catalog as m; m.main(['--site','ubereats','--shard',os.environ.get('UE_SHARD','0/8'),'--no-enrich'])` |
| Module | `unifyd/ue_catalog.py` — 1009 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | free |
| Memory / timeout | 4096 MB / 21600 s |
| Shards | 8 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** COLD getStoreV1 + getMenuItemV1 over the 502k-store sitemap universe; shardable (UE_SHARD=i/N), resumable, no caps. Headful ubereats.py archived as the zone crawler.


## 2. Transport

| constant | value |
|---|---|
| `MENU_API` | `https://www.ubereats.com/_p/api/getMenuItemV1` |


**Depends on** `blocks`, `extract_qa`, `getstore`, `identity_router`, `idset`, `ladder`, `observe`, `pace`, `raw_capture`, `resi`, `ubereats`, `value_rules`, `warehouse`


## 3. What it lands


### `ubereats_products_parts`

29,901,954 rows · 21 columns · 3,832 partitions


| column | type |
|---|---|
| `store_uuid` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `source` | `VARCHAR` |
| `item_uuid` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `gtin` | `VARCHAR` |
| `price` | `DOUBLE` |
| `list_price` | `DOUBLE` |
| `promo` | `VARCHAR` |
| `size` | `VARCHAR` |
| `abv` | `DOUBLE` |
| `in_stock` | `BOOLEAN` |
| `stock_label` | `VARCHAR` |
| `category` | `VARCHAR` |
| `section` | `VARCHAR` |
| `subsection` | `VARCHAR` |
| `section_name` | `VARCHAR` |
| `subsection_name` | `VARCHAR` |
| `category_path` | `VARCHAR` |


## 4. `ue_catalog.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
ue_catalog.py — the UberEats/Postmates catalog sweep, store-list driven, headless, SHARDABLE.

WHY THIS EXISTS
---------------
The registered `ubereats` source ran `ubereats.crawl(max_stores=1000)`: a headful browser discovering
stores by ZONE. Against a **502,212-store** universe that is 0.2%, and the bound lived in the registry
where nobody reads it — the source presented itself as "UberEats" while covering a rounding error of it.
We don't do caps: a scrape (or a designed series of them) covers its universe inside a day, and if one
worker can't, the answer is parallelism, never truncation.

THE ARITHMETIC (do this before writing a crawler)
    502,212 stores / 86,400s = ~5.8 stores/sec sustained to finish in a day.
A headful browser cannot approach that and cannot be sharded — it discovers stores by zone rather than
taking a list. So this uses the path that can:

  • `ubereats_sitemap` (502,212 rows) is the UNIVERSE — already harvested, $0, refreshes cleanly.
  • `getstore.fetch_store()` is a COLD curl_cffi POST to getStoreV1: no browser, no Bright Data, no
    warmed cookie. The sitemap's url id IS base64url(uuid bytes), so `url_id_to_uuid()` converts with
    no lookup — which is what makes the whole universe directly addressable.
  • `ubereats._items_from_store()` already parses the getStoreV1 catalog shape (price, promo, size/ABV,
    GTIN where present). Both halves existed; they had simply never been connected.

Being list-driven is what makes it SHARDABLE: `--shard i/N` splits the universe deterministically by a
stable hash of the store id, so N ephemeral machines cover disjoint slices with no coordination. That is
the "series of scrapes" — one job per shard, each resumable, all landing to the same tables.

RESUME + LANDING
Same contract as abc_fws_scraper, for the same reasons: land in BATCHES and checkpoint the completed
store ids, so a killed shard keeps everything it fetched and the next run continues instead of
restarting. A sweep that can only land at the end throws away hours on any interruption.

    python ue_catalog.py                      # whole universe, one process
    python ue_catalog.py --shard 3/16         # shard 3 of 16 (what the fleet runs)
    python ue_catalog.py --site postmates     # same recipe, different domain
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
