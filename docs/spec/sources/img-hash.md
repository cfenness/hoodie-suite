# Product image hashes (cheap divergence tier) — `img-hash`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `img-hash` |
| Runs | `import img_hash as m; m.build_all()` |
| Module | `unifyd/img_hash.py` — 176 lines |
| Cadence | weekly |
| Enabled | no — does not run on a cadence |
| Executor class | `headless` |
| Cost class | free |
| Memory / timeout | 4096 MB / 21600 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `pillow` |
| Unit test | **none** |


**Registry note.** fetch+hash product images across the retail catalogs; resumable (skips sku already hashed). DISABLED pending a first sized run — the image universe has not been counted, and this fetches every one of them.


## 2. Transport

_No literal endpoint constant in `img_hash.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `img_hash`

**Has never landed.** `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/img_hash.parquet' in region 'auto' (HTTP 404 Not Found)`

This is a registered source whose table does not exist in the warehouse — it has never completed a successful run, or it writes under a different name than the registry declares.


## 4. `img_hash.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
img_hash.py — the CHEAP twin of img_embed: perceptual hashes of product images, on pillow alone.

WHY THIS EXISTS ALONGSIDE img_embed
  `img_embed` answers "are these two photographs of the same product?" — a semantic question that
  genuinely needs CLIP, and CLIP needs torch, which this image does not ship. That dependency is why
  `img_vec` has never been populated.

  Cross-retailer asset divergence mostly asks a much cheaper question: **is this the same FILE?**
  When Kroger and Total Wine both show a product, they are usually both showing the supplier's
  syndicated JPEG, re-encoded and resized on the way through. A perceptual hash is exactly the right
  instrument for that — it survives re-encoding and scaling, and it needs nothing beyond pillow,
  which the image already carries.

THE ASYMMETRY THAT MATTERS, AND WHICH THE CALLER MUST RESPECT
  A hash MATCH is strong evidence: two images that hash within a few bits really are the same file.
  A hash SPLIT is weak evidence: the same pack photographed twice — different angle, lighting,
  background — hashes far apart. So this tier can confirm sameness confidently and can only ever
  RAISE A CANDIDATE for difference. `asset_divergence` encodes that as a separate verdict
  (`divergent_unconfirmed`) rather than letting a hash split masquerade as a packaging change.

  This is also why the well-known "every amber bottle hashes alike" problem
  ([[image-match-signal]]) does not bite here: that failure is about telling DIFFERENT products
  apart, and everything compared here is already inside one UPC.

    python img_hash.py build --source binnys_products
    python img_hash.py build --all
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
