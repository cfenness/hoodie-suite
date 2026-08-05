# ABC FW&S (catalog) — superseded by abc-fws — `abc-catalog`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `abc-catalog` |
| Runs | `import abc_catalog as m; m.run()` |
| Module | `unifyd/abc_catalog.py` — 88 lines |
| Cadence | weekly |
| Enabled | no — does not run on a cadence |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** SUPERSEDED — abc-fws lands abc_catalog from the same crawl (one fetch, both layers)


## 2. Transport

_No literal endpoint constant in `abc_catalog.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `abc_fws_scraper`, `runlog`, `warehouse`


## 3. What it lands


### `abc_catalog`

14,098 rows · 7 columns


| column | type |
|---|---|
| `sku` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `size` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `price` | `DOUBLE` |
| `url` | `VARCHAR` |


**Written by** `abc_catalog.py:77` (write_accumulate), `abc_catalog.py:68` (write_accumulate), `abc_fws_scraper.py:435` (write_accumulate)


## 4. `abc_catalog.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
abc_catalog.py — the ABC FWS PRODUCT CATALOG (sku -> name / brand / size / upc / price).

The ABC availability tracker (abc_fws_scraper) reads per-store in/out but never captures the product NAME, so
abc_products has 1.87M rows with blank name/brand — useless for master corroboration. This pass reuses the
scraper's sitemap harvest + fetch, pulls og:title / brand / gtin / price off each BigCommerce product page
(they render fine here), and lands abc_catalog — the single biggest independent corroboration source for the
master (master_ttb), on ~13.9k real products with UPCs.

    python abc_catalog.py --limit 200     # bounded proof
    python abc_catalog.py                  # full catalog (concurrent, ~20-40 min)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
