# Cross-source identity merge (overlay, precision-gated) — `xsource-match`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `xsource-match` |
| Runs | `import xsource_match as m; m.build()` |
| Module | `unifyd/xsource_match.py` — 316 lines |
| Cadence | every 168h |
| Enabled | no — does not run on a cadence |
| Executor class | `build` |
| Cost class | free |
| Memory / timeout | 8192 MB / 7200 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/xsource_match_test.py` |


**Registry note.** signature = brand_key + name_sig + size, UPC conflict always wins. Does NOT clear its 0.98 precision bar (0.233 measured) — needs a human-labelled gold set before another attempt, since the sources that need merging carry no UPC.


## 2. Transport

_No literal endpoint constant in `xsource_match.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `precleanse`, `warehouse`


## 3. What it lands


### `xsource_identity`

**Has never landed.** `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/xsource_identity.parquet' in region 'auto' (HTTP 404 Not Found)`

This is a registered source whose table does not exist in the warehouse — it has never completed a successful run, or it writes under a different name than the registry declares.


## 4. `xsource_match.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
xsource_match.py — merge the master's cross-source over-splits, as an OVERLAY.

THE MEASUREMENT THIS EXISTS FOR
  Measured live 2026-08-04 across binnys / abc / total-wine / haskells / specs, restricted to
  fifteen brands that unquestionably sit on every one of those shelves:

      signatures spanning >=2 sources        77
      split across >1 master identity        56   (73%)

  "Tito's Handmade Vodka 750ml" has two identities. So does Bacardi Gold 750ml, and Hennessy VS at
  375ml, 1000ml and 1750ml separately. The consequence shows up everywhere downstream — 98.5% of
  items carrying a product image are seen by exactly ONE source, which reads as "no retailer overlap"
  when it is really "we did not merge them" ([[master-fanout-brand-resolution]]).

WHY AN OVERLAY AND NOT A FIX TO THE MASTER
  Landed data is never rewritten ([[normalization-scout]]); corrections are a translation layer. And
  the master is append-only and versioned ([[append-only-versioned-master]]). So this lands
  `xsource_identity` — a mapping from the master's `resolved_id` to a merged `xsource_id` — which a
  consumer COALESCEs on, exactly the way `canon_identity` overlays `item_key` today. Nothing is
  deleted, nothing is renumbered, and turning it off is a one-line change at the read site.

THE MERGE RULE, AND WHY IT IS THIS CONSERVATIVE
  Two identities merge only when they agree on every SHELF DISCRIMINATOR
  ([[discriminator-identity-model]]): brand key, product-name signature, and size. All three must be
  PRESENT — a missing size is not a wildcard, it is a refusal, because "Absolut Citron" without a
  size is not an item, it is a product, and merging across sizes would destroy the item grain the
  master is counted at ([[master-item-grain]]).

  And a UPC CONFLICT always wins. If two rows carry different explicit UPCs they are different
  items, whatever their names look like — the names are what is unreliable here, not the barcode.
  This is the guard that stops "Bogle Merlot" and "Bogle Cab" style look-alikes from collapsing.

  There is no fuzzy tier. Every merge is an exact match on a normalized signature, so a merge can
  always be explained by showing the two signatures.

MEASURED RESULT — THIS RULE DOES NOT CURRENTLY CLEAR ITS OWN BAR
  Run against the real master (binnys / abc / total-wine / haskells / specs, 67,099 distinct rows)
  on 2026-08-04:

      merges proposed   1,003 identities into 514 groups
      scored pairs         60      (the rest unscoreable — see below)
      PRECISION         0.233      true 14 / false 46
      recall            0.160
      -> refused to land (bar 0.98)

  So a brand + name-signature + size match is NOT sufficient on retail product names, and the honest
  status of this module is: it proposes merges, it measures itself, and it declines to ship them.
  It is registered DISABLED and lands nothing.

  Two things the measurement also exposed, both worth fixing before another attempt:
    • 59,455 of 67,099 rows are UNSCOREABLE. binnys / abc / total-wine carry no UPC at all — the
      very sources that need merging — so gold has to come from the master's own upc/gtin (it does
      now) and even then covers a thin slice. A human-labelled set is probably required.
    • a `resolved_id` can legitimately span several UPCs, so "two ids whose UPC sets do not
      intersect" is a harsher test than "these are different items". Some of the 46 false pairs are
      likely gold artefacts rather than real errors — which is itself a reason not to trust the
      0.233 as the final word, in either direction.

PRECISION IS MEASURED, NOT ASSUMED
  `score()` builds gold from the data itself, the same way `master_quality` does: two rows sharing a
  UPC SHOULD merge (recall), two rows with different UPCs must NOT (precision). The overlay ships
  with its measured score attached, and `build()` refuses to land a merge set whose precision falls
  below `MIN_PRECISION` — a matching layer that silently degrades identity is worse than none.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
