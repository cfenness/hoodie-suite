# Distributor item crosswalk (Overlay Tier-3 spine) — `build-dist-xwalk`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-dist-xwalk` |
| Runs | `import dist_xwalk as m; m.build()` |
| Module | `unifyd/dist_xwalk.py` — 149 lines |
| Cadence | every 24h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** dist_item_code|retail_upc → canon_item_id from vip_brandbuilder_items + bbg_products


## 2. Transport

_No literal endpoint constant in `dist_xwalk.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `dist_item_xwalk`

755,221 rows · 10 columns


| column | type | filled |
|---|---|---|
| `distributor_id` | `VARCHAR` | 100.0% |
| `distributor_name` | `VARCHAR` | 100.0% |
| `dist_item_code` | `VARCHAR` | 100.0% |
| `dist_item_key` | `VARCHAR` | 100.0% |
| `retail_upc` | `VARCHAR` | 97.2% |
| `canon_item_id` | `BIGINT` | 97.2% |
| `brand` | `VARCHAR` | 100.0% |
| `product_name` | `VARCHAR` | 100.0% |
| `size_raw` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

**Written by** `dist_xwalk.py:119` (write_parquet)


## 4. `dist_xwalk.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
dist_xwalk.py — build `dist_item_xwalk`, the Tier-3 match spine (OVERLAY-DESIGN §6).

The files people upload to Overlay are DISTRIBUTOR-SHAPED: their own item numbers, a supplier
code, sometimes a retail UPC. Tier 1 (UPC) only reaches the rows that carry a good UPC. Tier 3 is
the one that lands hardest with a distributor buyer, because we match on *their own item numbers* —
and it's buildable today from catalogs we already pull:

    distributor_id | distributor_name | dist_item_code | retail_upc | canon_item_id | source

  · `vip_brandbuilder_items` (vtinfo_bbs.py)  — VIP Brand Builder, ~300 distributors reachable,
        package-grain with `dist_item_code` + a zero-padded retail UPC. The richest Tier-3 source.
  · `bbg_products` (bbg_salsify.py)           — Salsify tenant catalogs (Breakthru today; the recipe
        is parameterized by SITE, so each new tenant lands in this same grain).

`canon_item_id` is resolved the same way `build_item_identity` resolves it — the digits-only UPC as
a bigint — so a Tier-3 hit and a Tier-1 hit land on the SAME identity by construction rather than by
a second join. A crosswalk row with no resolvable UPC is still written: the distributor's item code
and name are real, and Tier 3 can return a *display* even where identity is not yet resolvable. That
is the honest half-match, and it's marked as such (`canon_item_id` NULL) rather than dropped.

    python dist_xwalk.py            # rebuild dist_item_xwalk from the landed distributor catalogs
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
