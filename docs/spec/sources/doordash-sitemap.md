# DoorDash store universe — `doordash-sitemap`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `doordash-sitemap` |
| Runs | `import doordash_sitemap as m; m.run()` |
| Module | `unifyd/doordash_sitemap.py` — 194 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / 7200 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | `unifyd/doordash_sitemap_test.py` |


**Registry note.** $0 national store spine from DoorDash's own sitemaps (curl_cffi+ISP); feeds naop + retail


## 2. Transport

| constant | value |
|---|---|
| `STORE_INDEX` | `https://www.doordash.com/sitemap-store-doordash-index.xml` |


**Depends on** `resi`, `warehouse`


## 3. What it lands


### `doordash_stores`

773,357 rows · 7 columns


| column | type | filled |
|---|---|---|
| `store_id` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `city` | `VARCHAR` | 100.0% |
| `state` | `VARCHAR` | 99.9% |
| `url` | `VARCHAR` | 100.0% |
| `type` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

**Written by** `doordash_discover.py:133` (write_accumulate), `doordash_sitemap.py:142` (write_accumulate)


## 4. `doordash_sitemap.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
doordash_sitemap.py — the $0 national DoorDash store universe, straight from DoorDash's OWN sitemaps.

robots.txt advertises `sitemap-store-doordash-index.xml` → ~130 per-state sub-sitemaps
(`sitemap-doordash-<st>-stores.xml`), each ~9–10k store URLs of the form
`/store/<name-slug>-<store_id>/`. We harvest every store id + name-slug + state at ZERO cost — curl_cffi
Safari-17 TLS impersonation through the flat-rate residential ISP pool (the same path that fetches the
menus), no geo pins, no BD Browser, no Google Maps. This replaces the metered `Proxy.setLocation` grid /
BD-Browser discovery: DoorDash publishes the whole store list, so we just read it.

This is the discovery SPINE that feeds doordash_naop (on-premise restaurant menus) and the retail
connectors nationally. Lands `doordash_stores` (accumulate, key=store_id) — the same table
doordash_discover fans from.

    python doordash_sitemap.py                 # every state
    python doordash_sitemap.py --states or,fl  # bounded
    python doordash_sitemap.py --cap 500       # cap stores/state (smoke)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
