# Asset divergence (cross-retailer pack disagreement) — `asset-divergence`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `asset-divergence` |
| Runs | `import asset_divergence as m; m.build()` |
| Module | `unifyd/asset_divergence.py` — 462 lines |
| Cadence | every 168h |
| Enabled | no — does not run on a cadence |
| Executor class | `build` |
| Cost class | free |
| Memory / timeout | 8192 MB / 7200 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/asset_divergence_test.py` |


**Registry note.** DISABLED until img-hash has run — it is a derived read over the image tiers and produces nothing until one is populated. Runs on dHash (pillow) and upgrades to CLIP wherever img_vec exists. Staleness withheld without measured precision (backtest()).


## 2. Transport

_No literal endpoint constant in `asset_divergence.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `img_embed`, `img_hash`, `warehouse`


## 3. What it lands


### `asset_divergence`

**Has never landed.** `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/asset_divergence.parquet' in region 'auto' (HTTP 404 Not Found)`

This is a registered source whose table does not exist in the warehouse — it has never completed a successful run, or it writes under a different name than the registry declares.


## 4. `asset_divergence.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
asset_divergence.py — where do chains disagree about what a product LOOKS like?

THE QUESTION
  One item (one UPC) is listed by a dozen retailers, each with its own product image. Usually those
  images are the same supplier pack shot passed down the syndication chain. Sometimes they are not —
  one chain is still showing packaging the brand retired two years ago. That gap is invisible to the
  supplier (they see what they published, not what the trade executed) and invisible to the retailer
  (they see their own set), and it is visible here because this is the only place both sides land.

WHAT THIS DOES AND, MORE IMPORTANTLY, WHAT IT REFUSES TO DO
  It clusters an item's images across sources and reports the DIVERGENCE — how many distinct looks
  are live, who shows which, since when. That part is measurable and lands.

  It does NOT tell you which one is stale. Not yet, and not by default. The reason is specific: at
  this threshold "two photographs of the same bottle" and "two different packs" are not reliably
  separable. `img_embed` measured same-product-different-photo at cosine median ~0.76 — that is the
  distribution a *benign* difference already occupies, so a repack sits somewhere on top of it and
  nobody has labelled where. Calling the difference `stale` on an unmeasured threshold would mean
  telling a brand team their retail execution is broken on the strength of a lighting change.

  So `stale_candidate` is None until `backtest()` measures precision against a labelled set, exactly
  the gate `overlay_detect` applies to its heuristics: a rule with no measured precision RUNS BUT
  STAYS SILENT. `withheld_reason` says so on every row.

WHAT IS DETERMINISTIC HERE, AND THEREFORE SAFE TO SHOW
  Not the similarity verdict — the EVIDENCE around it:
    • how many distinct clusters an item's images form, at a stated threshold
    • which sources are in each cluster, and how many
    • first-seen / last-seen per cluster, from the observation history
  "Five chains show look A since 2024, one chain shows look B and has not been re-observed since
  2022" is a defensible sentence built from counts and dates. It is also the sentence that makes a
  supplier conversation concrete, without ever claiming which pack is correct — which is the
  supplier's own data to supply.

IDENTITY IS THE MASTER'S, NOT A RAW UPC
  The first cut grouped by the UPC on the source row, and it could not see the sources that matter:
  binnys_products, abc_products and total_wine_products carry 35k images between them and have NO
  upc column at all — they key on a retailer SKU. Measured live, that version reached 1,172 items.

  Identity therefore comes from the master, which exists to answer exactly this:
  `xwalk_source_sku` (source + product_id -> item_key) then `dim_sku.resolved_id`. Measured on the
  same data:

      item_key    (md5 hard key)          251,193 items ->   515 on >=3 sources,  3,366 images
      resolved_id (collapsed identity)     89,016 items -> 1,104 on >=3 sources, 18,302 images

  `resolved_id` wins because the md5 key OVER-SPLITS — the same product stated differently by two
  sources gets two keys, and reuniting them is the whole job of the resolved identity. Divergence is
  a cross-source measure, so it lives or dies on that collapse.

  Every row records `identity_method`, because a divergence found under `upc` and one found under
  `resolved_id` are not equally trustworthy and must not be pooled silently.

WHAT THIS MEASUREMENT ACTUALLY EXPOSED
  98.5% of items with an image are seen by ONE source. That is not a fact about retail — Kroger and
  Total Wine plainly both sell Absolut Citron 1750 — it is the master's fan-out
  ([[master-fanout-brand-resolution]]). So the ceiling on this detector is master identity
  resolution, not images, not embeddings, and not compute: the whole current working set hashes in
  under two hours.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
