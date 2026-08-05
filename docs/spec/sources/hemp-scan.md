# Hemp products — `hemp-scan`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `hemp-scan` |
| Runs | `import hemp_scan as m; m.main([])` |
| Module | `unifyd/hemp_scan.py` — 142 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** hemp-bev feed


## 2. Transport

_No literal endpoint constant in `hemp_scan.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `hemp_products`

4,040 rows · 11 columns


| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 79.7% |
| `category` | `VARCHAR` | 84.6% |
| `upc` | `VARCHAR` | 14.8% |
| `size_ml` | `VARCHAR` | **3.3%** |
| `price` | `VARCHAR` | 33.8% |
| `image` | `VARCHAR` | 71.4% |
| `state` | `VARCHAR` | **0%** ‹never populated› |
| `url` | `VARCHAR` | 15.1% |
| `signal` | `VARCHAR` | 100.0% |

Fill measured over **full table** (4,040 rows).

> **1 column never populated:** `state`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `hemp_scan.py:120` (write_parquet)


## 4. `hemp_scan.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
hemp_scan.py — find HEMP / THC BEVERAGE inventory inside the catalogs we already scrape.

Hemp-derived THC/CBD beverages (low-dose seltzers, tonics, sodas, mocktails) are increasingly stocked by
mainstream liquor/grocery, so the fastest hemp coverage is to MINE what we already hold before building new
pulls. Priority = BEVERAGES (not gummies/tinctures/flower). A product qualifies when a cannabinoid signal
(THC/CBD/hemp/delta-9/cannabinoid, or an "N mg" dose) co-occurs with a beverage form (seltzer/soda/tonic/
drink/…), OR it's a known hemp-beverage brand. Lands `hemp_products` (source, name, brand, category, upc,
size_ml, price, image, state, signal) + prints coverage by source. Reusable across every *_products catalog.

    python hemp_scan.py                 # scan all catalogs → hemp_products
    python hemp_scan.py --source offprem_products
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
