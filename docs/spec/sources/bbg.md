# Breakthru Beverage (Salsify catalog) — folded into `salsify` — `bbg`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `bbg` |
| Runs | `import salsify as m; m.pull(catalog='bbg')` |
| Module | `unifyd/salsify.py` — 975 lines |
| Cadence | daily |
| Enabled | no — does not run on a cadence |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/salsify_test.py` |


**Registry note.** SUPERSEDED as a separate source — `salsify` pulls every seeded catalog including BBG, sequentially, so exactly one process ever merges salsify_products. Manually runnable for a BBG-only pull, but never WHILE `salsify` is running


## 2. Transport

| constant | value |
|---|---|
| `HOST` | `https://sites.salsify.com` |


**Depends on** `warehouse`


## 3. What it lands


### `salsify_products`

63,889 rows · 35 columns


| column | type | filled |
|---|---|---|
| `catalog_id` | `VARCHAR` | 100.0% |
| `catalog_name` | `VARCHAR` | 100.0% |
| `org_id` | `VARCHAR` | 100.0% |
| `site_id` | `VARCHAR` | 100.0% |
| `owner` | `VARCHAR` | 100.0% |
| `tier` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `dist_item_code` | `VARCHAR` | 100.0% |
| `system_id` | `VARCHAR` | 100.0% |
| `grouping_key` | `VARCHAR` | 100.0% |
| `sku_upc` | `VARCHAR` | 16.6% |
| `title` | `VARCHAR` | 100.0% |
| `item_description` | `VARCHAR` | 50.8% |
| `brand` | `VARCHAR` | 100.0% |
| `sort_value` | `VARCHAR` | 100.0% |
| `supplier` | `VARCHAR` | 87.0% |
| `brand_owner` | `VARCHAR` | 12.0% |
| `category` | `VARCHAR` | 100.0% |
| `sub_category` | `VARCHAR` | 12.0% |
| `size_text` | `VARCHAR` | 100.0% |
| `size_ml` | `BIGINT` | 91.7% |
| `abv` | `DOUBLE` | 100.0% |
| `proof` | `DOUBLE` | 33.3% |
| `units_per_case` | `VARCHAR` | 87.0% |
| `country` | `VARCHAR` | 12.0% |
| `region` | `VARCHAR` | 67.4% |
| `varietal` | `VARCHAR` | 48.5% |
| `flavor` | `VARCHAR` | 87.0% |
| `market_region` | `VARCHAR` | 86.2% |
| `image` | `VARCHAR` | 83.8% |
| `image_count` | `BIGINT` | 100.0% |
| `property_count` | `BIGINT` | 100.0% |
| `properties_hash` | `VARCHAR` | 100.0% |
| `product_url` | `VARCHAR` | 100.0% |
| `pulled_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (63,889 rows).

**Written by** `salsify.py:858` (write_accumulate)


### `salsify_properties`

2,870,998 rows · 10 columns · 314 partitions


| column | type | filled |
|---|---|---|
| `catalog_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `group` | `VARCHAR` | 100.0% |
| `property` | `VARCHAR` | 100.0% |
| `label` | `VARCHAR` | 100.0% |
| `value_index` | `BIGINT` | 100.0% |
| `value` | `VARCHAR` | 100.0% |
| `asset_name` | `VARCHAR` | 6.4% |
| `day` | `VARCHAR` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |

Fill measured over **newest 40 of 314 partitions** (925,273 rows).

**Written by** `salsify.py:873` (write_partition)


## 4. `salsify.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
salsify.py — Salsify Sites: the PLATFORM recipe for every public catalog on sites.salsify.com.

WHY THIS IS A PLATFORM RECIPE (not a one-off scrape):
  Salsify is the PIM that a large slice of CPG brands and beverage-alcohol wholesalers publish their
  public catalog from, and every one of those catalogs is the SAME Next.js SSG app under
  `sites.salsify.com/<orgId>/<siteId>/`. Prove the shape once and every site on the platform is free:

      root HTML     -> __NEXT_DATA__ : buildId + catalogName + totalProducts/totalPages + facets
      list page 1   -> _next/data/<buildId>/index.json                       (page 1 is index, NOT products/1)
      list page N>1 -> _next/data/<buildId>/products/<N>.json?params=products&params=<N>
      product       -> _next/data/<buildId>/product/<id>/<slug>.json?id=<id>&title=<slug>
      sitemap       -> <site>/sitemap_1.xml   (when published: the whole id/slug universe in one fetch)

  AND THE URL LETS US LOOP IT. `https://sites.salsify.com/robots.txt` publishes
  `sitemap_index.xml` — a live directory of every PUBLIC catalog on the platform (519 sites across
  118 orgs as of 2026-08-03). `discover()` walks it, probes each root, and lands the directory to
  `salsify_catalogs`; `pull(catalog=...)` then crawls any one of them with the same code.

WHAT WE KEEP: everything the site exposes. The catalogs do NOT share a property namespace — BBG
  publishes SAP wholesaler attributes (Material ID, Supplier, Bottles Per Case, SAP Varietal…),
  Sazerac publishes a full GS1/GDSN item master (tradeItemgtin, percentageOfAlcoholByVolume,
  alcoholProof, net content + UOM, weights/dimensions, ingredients, nutrients, allergens, closure,
  country of origin, even the TTB COLA id). A fixed column set would drop most of that, so capture is
  two-grained ([[retailer-full-capture]] — grain governs where data is modelled, never whether it's kept):

    salsify_products    — one row per product: the canonical spine + the bev-alc fields we map across
                          namespaces. Accumulating catalog (write_accumulate), thin columns only.
    salsify_properties  — one row per (product, property, value): EVERY property of every property set,
                          every filter facet, every digital asset, every value of a multi-value field —
                          whatever the catalog happens to call them. Append-only + date-partitioned,
                          exactly like raw_payloads, because a property value is an EVENT (what the
                          source said today), not a mutable attribute ([[payloads-are-events]]). Only
                          products whose property fingerprint MOVED are re-written, so a daily cadence
                          costs the diff, not the catalog.
    salsify_catalogs    — the platform directory: every public site, its org/site ids, name, size.

BREAKTHRU (`bbg`): the seed and the reason for the split. `id` / `Material ID` IS Breakthru's
  distributor item code (their SAP material number) and `subtitle` / `Marketing Name` IS their own item
  description — both are landed under those names (`dist_item_code`, `item_description`) instead of being
  flattened into `id`/`brand` the way the superseded bbg_salsify.py did.

GRAIN: one row per product per catalog. Identity = catalog_id | product_id. Resumable (skips products
  already detailed), polite, stdlib-only, headless. Self-reports `degraded` — never bad data — when the
  site reports products but we enumerate none, when detail fetches collapse, or when identity/property
  fill drops off a cliff.

CLI:  python salsify.py --catalog bbg                 # full BBG catalog
      python salsify.py --catalog bbg --pages 5       # smoke (5 list pages)
      python salsify.py --discover                    # refresh the public-catalog directory
      python salsify.py --discover --probe-all        # …and probe every site (slower, richer)
      python salsify.py --list                        # seeded catalogs
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
