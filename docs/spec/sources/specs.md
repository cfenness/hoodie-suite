# Spec's — `specs`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `specs` |
| Runs | `import specs_scraper as m; m.pull(crawl_all=True)` |
| Module | `unifyd/specs_scraper.py` — 451 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** Next.js sitemap


## 2. Transport

| constant | value |
|---|---|
| `BASE` | `https://specsonline.com` |


**Depends on** `abc_fws_scraper`, `observe`, `polite`, `runlog`, `warehouse`


## 3. What it lands


### `specs_products`

1,029 rows · 23 columns


| column | type |
|---|---|
| `sku` | `VARCHAR` |
| `slug` | `VARCHAR` |
| `url` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `type` | `VARCHAR` |
| `varietal` | `VARCHAR` |
| `abv` | `VARCHAR` |
| `origin` | `VARCHAR` |
| `region` | `VARCHAR` |
| `state` | `VARCHAR` |
| `vintage` | `VARCHAR` |
| `tasting_notes` | `VARCHAR` |
| `pairs_with` | `VARCHAR` |
| `description` | `VARCHAR` |
| `price` | `DOUBLE` |
| `upc` | `VARCHAR` |
| `image` | `VARCHAR` |
| `in_stock_stores` | `BIGINT` |
| `store_count` | `BIGINT` |
| `units_total` | `BIGINT` |
| `stores_tracked` | `BIGINT` |
| `raw_json` | `VARCHAR` |


**Written by** `specs_scraper.py:419` (write_accumulate), `specs_scraper.py:410` (write_accumulate), `specs_scraper.py:415` (write_parquet)


## 4. `specs_scraper.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
specs_scraper.py — STORE-LEVEL price + INVENTORY-COUNT tracker for Spec's (specsonline.com).

Spec's serves bots (200) and embeds a per-store `variants` object right in the product
page — ~190 store variants, each with `inStock` (bool) and `unitPrice` (cents) keyed by a store code
in `code` ("<storeCode>-<sku>"). That page block gives per-store price + in/out for free in
ONE fetch. The actual UNIT COUNT lives one hop away: the PDP calls an inventory API
`GET /api/products/stock/{storeCode}-{upc}/` → {status:"ok", available:N, tracked:bool}.
So Spec's is a real COUNTS source (like Binny's/ABC), not just in/out — we read the number
per (store, product) from that endpoint. Snapshot keyed `sku|storeCode`, carrying `qty`.

connId: `specs`. Harvest product URLs from the sitemap (direct), poll a deterministic
sample, diff vs the prior snapshot. Self-reports `degraded` if the `variants` block can't
be parsed on most pages (markup drift).

Counts fan out one call per (in-stock store, product) — SPECS_QTY=1 default; set
SPECS_COUNT_STORES="0,5,35" to restrict counts to a focus set of stores (bounds request
volume on a full crawl), else all in-stock stores are counted.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
