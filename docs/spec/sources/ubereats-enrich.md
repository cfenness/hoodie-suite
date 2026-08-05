# Uber Eats item UPC/GTIN backfill (sharded) — `ubereats-enrich`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `ubereats-enrich` |
| Runs | `import os; os.environ['LADDER_MAX_RUNG']='impersonate'; import ue_enrich as m; m.main(['--site','ubereats','--shard',os.environ.get('UE_SHARD','0/8')])` |
| Module | `unifyd/ue_enrich.py` — 221 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | free |
| Memory / timeout | 4096 MB / 21600 s |
| Shards | 8 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** separate clock from the sweep: static per-item attributes, fetched once ever


## 2. Transport

_No literal endpoint constant in `ue_enrich.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `fold`, `raw_capture`, `ubereats`, `ue_catalog`, `warehouse`


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


## 4. `ue_enrich.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
ue_enrich.py — drain the backlog of un-resolved UberEats items, off the critical path.

WHY THIS IS A SEPARATE JOB. The store sweep is ONE request per store: 502,212 requests, ~30 minutes
across the fleet. Enrichment is one request per NEW item, and at a measured ~82 items/store that turns
the same job into ~41.7M requests — and it ran SERIALLY inside each store's worker thread, ~18.5s per
store, which matched the observed fleet rate exactly. The pull we were measuring was a 30-minute job
wearing a 46-hour coat.

They separate cleanly because they answer different questions:
  * price / stock / promo are VOLATILE, and arrive free with the catalog call the sweep already makes.
  * UPC / GTIN / brand / size / ABV are STATIC per item — fetch once, ever.

So the sweep stays fast and complete on a daily clock, and this drains the static-attribute backlog
continuously. Day one is a real backfill; after that only genuinely-new items cost anything, because
a resolved item is never re-fetched.

CONTRACT (the same rules as the sweep, learned the hard way today):
  * append-only PARTS, never write_accumulate — concurrent shards merging a catalog lose rows.
  * checkpointed, so a killed shard resumes instead of restarting.
  * the work-list is a QUERY, not a cap: everything still unresolved, sharded by a stable hash.
  * reports its own denominator so completeness is graded against the JOB, not a watermark.

    python3 ue_enrich.py --shard 0/8
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
