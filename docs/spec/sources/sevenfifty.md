# SevenFifty storefronts (distributor catalogs) — `sevenfifty`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `sevenfifty` |
| Runs | `import sevenfifty as m; m.pull()` |
| Module | `unifyd/sevenfifty.py` — 210 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** <slug>.storefronts.site/search.json — distributor item master (SKUs), no auth (prices need partner login); parameterized by storefront slug (johnsonbrothers seed). Add slugs to STOREFRONTS.


## 2. Transport

_No literal endpoint constant in `sevenfifty.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `sevenfifty_items`

25,785 rows · 28 columns


| column | type | filled |
|---|---|---|
| `storefront` | `VARCHAR` | 100.0% |
| `distributor` | `VARCHAR` | 100.0% |
| `sku` | `VARCHAR` | 100.0% |
| `sevenfifty_id` | `BIGINT` | 100.0% |
| `token` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `name_full` | `VARCHAR` | 100.0% |
| `producer` | `VARCHAR` | 100.0% |
| `supplier` | `VARCHAR` | 100.0% |
| `product_type` | `VARCHAR` | 100.0% |
| `style` | `VARCHAR` | 100.0% |
| `style_line` | `VARCHAR` | 100.0% |
| `subtype` | `VARCHAR` | 100.0% |
| `appellation` | `VARCHAR` | 28.6% |
| `country` | `VARCHAR` | 100.0% |
| `region` | `VARCHAR` | 78.6% |
| `subregion` | `VARCHAR` | 15.1% |
| `size` | `DOUBLE` | 100.0% |
| `size_formatted` | `VARCHAR` | 100.0% |
| `case_size` | `BIGINT` | 100.0% |
| `container_type` | `VARCHAR` | 100.0% |
| `raw_materials` | `VARCHAR` | 43.7% |
| `status` | `VARCHAR` | 100.0% |
| `vendor_id` | `BIGINT` | 100.0% |
| `image_url` | `VARCHAR` | 99.6% |
| `thumbnail_url` | `VARCHAR` | 99.6% |
| `display_url` | `VARCHAR` | 100.0% |
| `pulled_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (25,785 rows).

**Written by** `sevenfifty.py:179` (write_accumulate)


## 4. `sevenfifty.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
sevenfifty.py — SevenFifty / Provi distributor **storefront** catalogs (*.storefronts.site).

WHY THIS IS A PLATFORM RECIPE (not a one-off scrape):
  SevenFifty (now Provi) powers the B2B "marketplace storefront" for a large slice of US
  beverage-alcohol DISTRIBUTORS. Every distributor storefront lives on the same platform,
  keyed only by its subdomain slug — and each one exposes a plain, **open JSON search API**:

      https://<slug>.storefronts.site/search.json?page=<n>&per_page=100
             &sort=score&direction=desc&searched_from=marketplace-storefronts

  No auth, no cookie: the catalog (the distributor's real item numbers / SKUs, producer,
  supplier, style, appellation, size, case pack, image) comes back for anyone. Only partner
  PRICING is gated behind login (the `prices` arrays are empty when unauthenticated) — so this
  is an **item-master** pull, not a price pull. `meta.total_pages` drives pagination.

  So proving ONE storefront proves the platform: point this at any slug and you get that
  distributor's whole book. Johnson Brothers (`johnsonbrothers`) is the seed — 25,590 items.
  This is a direct line to the big distributors' catalogs (Reyes, Breakthru, RNDC, Southern
  Glazer's, …) one slug at a time — the "prove the system once → every store on it is free"
  recipe payoff ([[system-scrape-recipes]]).

GRAIN: one row per **item** (a distributor SKU). Identity = storefront | sku. Snapshot per
  storefront → new / dropped since last pull. If the API reports items (`meta.total` > 0) but we
  extract 0 rows, or SKU fill collapses, the run self-reports `degraded` rather than emitting
  bad data.

CLI:  python sevenfifty.py --store johnsonbrothers
      python sevenfifty.py --store johnsonbrothers --cap 3   # first 3 pages only (smoke)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
