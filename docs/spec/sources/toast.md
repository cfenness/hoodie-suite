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


| column | type | filled |
|---|---|---|
| `guid` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `slug` | `VARCHAR` | 100.0% |
| `url` | `VARCHAR` | 100.0% |
| `state` | `VARCHAR` | **0%** ‹never populated› |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (85,284 rows).

> **1 column never populated:** `state`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `toast.py:101` (write_accumulate)


### `toast_beverages`

27,269 rows · 15 columns


| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `account` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `description` | `VARCHAR` | 22.6% |
| `price` | `DOUBLE` | 85.3% |
| `category` | `VARCHAR` | 100.0% |
| `is_alcoholic` | `BOOLEAN` | 100.0% |
| `root` | `VARCHAR` | 23.4% |
| `sub` | `VARCHAR` | 48.0% |
| `base_spirit` | `VARCHAR` | 22.6% |
| `beer_style` | `VARCHAR` | 7.8% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `price_basis` | `VARCHAR` | 100.0% |
| `captured` | `VARCHAR` | 100.0% |

Fill measured over **full table** (27,269 rows).

**Written by** `toast.py:205` (write_accumulate)


### `toast_menu_accounts`

2,059 rows · 13 columns


| column | type | filled |
|---|---|---|
| `guid` | `VARCHAR` | 100.0% |
| `account` | `VARCHAR` | 100.0% |
| `clean_name` | `VARCHAR` | 99.6% |
| `street` | `VARCHAR` | 99.0% |
| `city` | `VARCHAR` | 99.0% |
| `state` | `VARCHAR` | 99.4% |
| `phone` | `VARCHAR` | 99.0% |
| `lat` | `DOUBLE` | 99.0% |
| `lng` | `DOUBLE` | 99.0% |
| `serves_alcohol` | `BOOLEAN` | 100.0% |
| `n_beverages` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `captured` | `VARCHAR` | 100.0% |

Fill measured over **full table** (2,059 rows).

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
