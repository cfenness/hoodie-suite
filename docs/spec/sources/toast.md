# Toast own-menus — `toast`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `toast` |
| Runs | `import toast as m; m.run()` |
| Module | `unifyd/toast.py` — 243 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / 7200 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** $0 restaurant OWN menus from toasttab.com sitemaps (~100k); harvest + TOAST_LIMIT menu batches


## 2. Transport

| constant | value |
|---|---|
| `SITEMAP_INDEX` | `https://www.toasttab.com/local/sitemaps/index.xml` |


**Depends on** `cocktail_taxonomy`, `observe`, `outlet_ident`, `resi`, `warehouse`


## 3. What it lands


### `toast_outlets`

85,284 rows · 6 columns


| column | type |
|---|---|
| `guid` | `VARCHAR` |
| `name` | `VARCHAR` |
| `slug` | `VARCHAR` |
| `url` | `VARCHAR` |
| `state` | `VARCHAR` |
| `source` | `VARCHAR` |


**Written by** `toast.py:101` (write_accumulate)


### `toast_beverages`

27,269 rows · 15 columns


| column | type |
|---|---|
| `store` | `VARCHAR` |
| `account` | `VARCHAR` |
| `name` | `VARCHAR` |
| `description` | `VARCHAR` |
| `price` | `DOUBLE` |
| `category` | `VARCHAR` |
| `is_alcoholic` | `BOOLEAN` |
| `root` | `VARCHAR` |
| `sub` | `VARCHAR` |
| `base_spirit` | `VARCHAR` |
| `beer_style` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `source` | `VARCHAR` |
| `price_basis` | `VARCHAR` |
| `captured` | `VARCHAR` |


**Written by** `toast.py:205` (write_accumulate)


### `toast_menu_accounts`

2,059 rows · 13 columns


| column | type |
|---|---|
| `guid` | `VARCHAR` |
| `account` | `VARCHAR` |
| `clean_name` | `VARCHAR` |
| `street` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `phone` | `VARCHAR` |
| `lat` | `DOUBLE` |
| `lng` | `DOUBLE` |
| `serves_alcohol` | `BOOLEAN` |
| `n_beverages` | `BIGINT` |
| `source` | `VARCHAR` |
| `captured` | `VARCHAR` |


**Written by** `toast.py:207` (write_accumulate)


## 4. `toast.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
toast.py — restaurant OWN menus + an outlet spine from Toast (toasttab.com), $0.

Toast is the online-ordering platform behind ~100k US restaurants. Its local sitemaps
(`toasttab.com/local/sitemaps/index.xml` → `restaurant-pages*.xml`) list every restaurant's own-ordering
page `/local/order/<name-city-slug>/r-<guid>` — the restaurant's OWN menu (name/description/price), NOT the
delivery-inflated aggregator menu. We harvest the universe ($0 — curl_cffi Safari-17 + the flat ISP pool,
the same path that beats DoorDash's Forter), land `toast_outlets` (a source layer for the outlet
pre-mastering union), and parse each menu → beverages (via cocktail_taxonomy) into `toast_beverages`,
tagged source=toast / price_basis=menu so it stays SEPARABLE from the DoorDash delivery menus for
per-source freshness judging.

    python toast.py --harvest             # refresh the outlet universe (weekly)
    python toast.py --menus --limit 60    # pull the next batch of own-menus
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
