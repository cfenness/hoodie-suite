# Winebow (distributor) — `winebow`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `winebow` |
| Runs | `import winebow as m; m.pull()` |
| Module | `unifyd/winebow.py` — 103 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** portfolio


## 2. Transport

| constant | value |
|---|---|
| `BASE` | `https://www.winebow.com/our-brands` |


**Depends on** `warehouse`


## 3. What it lands


### `winebow_brands`

1,396 rows · 7 columns


| column | type | filled |
|---|---|---|
| `brand` | `VARCHAR` | 100.0% |
| `website` | `VARCHAR` | 66.8% |
| `logo` | `VARCHAR` | 100.0% |
| `importer` | `VARCHAR` | 100.0% |
| `country` | `VARCHAR` | **0%** ‹never populated› |
| `product_type` | `VARCHAR` | **0%** ‹never populated› |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (1,396 rows).

> **2 columns never populated:** `country`, `product_type`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `winebow.py:86` (write_parquet)


## 4. `winebow.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
winebow.py — Winebow importer/distributor brand portfolio (the supplier/importer tier).

Winebow is a major US wine + spirits IMPORTER/DISTRIBUTOR. Its public /our-brands page lists every brand it
carries (~1,394), and each card links to the BRAND'S OWN website. Two things we don't otherwise have:
  1. The IMPORTER→BRAND mapping — a real supplier-tier grouping (all these brands reach US shelves through
     Winebow), the deterministic supplier signal the plant/vendor codes couldn't give us.
  2. The producer WEBSITE per brand — feeds producer_site.py deep enrichment (cask/mash-bill/tasting/awards).

Drupal view, server-rendered (no JS needed), plain `?page=N` pagination at 12 cards/page. Lands
`winebow_brands` (brand, website, logo, importer, country, product_type). stdlib only, polite. This is the
first of the importer-portfolio class — the same shape works for other importers (add their base + card regex).
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
