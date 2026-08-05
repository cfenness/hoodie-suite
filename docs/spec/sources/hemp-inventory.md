# Hemp per-store inventory — `hemp-inventory`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `hemp-inventory` |
| Runs | `import hemp_inventory as m; m.main([])` |
| Module | `unifyd/hemp_inventory.py` — 147 lines |
| Cadence | daily |
| Enabled | no — does not run on a cadence |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** PARKED (2026-07): its base universe was starved — it read a phantom orlando_hemp_products table (now removed) + only the incidental Shopify subset of offprem_products; most rows had no count (oversell). Hemp is covered by hemp-finder (retailers) + hemp-scan (listings). Re-enable once pointed at a real Shopify hemp-store universe with a platform filter


## 2. Transport

_No literal endpoint constant in `hemp_inventory.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `hemp_scan`, `warehouse`


## 3. What it lands


### `hemp_inventory`

475 rows · 15 columns


| column | type | filled |
|---|---|---|
| `retailer` | `VARCHAR` | 100.0% |
| `base` | `VARCHAR` | 100.0% |
| `state` | `VARCHAR` | **0%** ‹never populated› |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `variant_id` | `VARCHAR` | 100.0% |
| `sku` | `VARCHAR` | 77.7% |
| `upc` | `VARCHAR` | **0%** ‹never populated› |
| `price` | `VARCHAR` | 100.0% |
| `available` | `BOOLEAN` | 100.0% |
| `qty` | `BIGINT` | 18.7% |
| `method` | `VARCHAR` | 100.0% |
| `signal` | `VARCHAR` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (475 rows).

> **2 columns never populated:** `state`, `upc`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `hemp_inventory.py:127` (write_accumulate)


## 4. `hemp_inventory.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
hemp_inventory.py — pull PER-STORE INVENTORY COUNTS for hemp beverages from Shopify hemp retailers.

The goal is inventory COUNTS, not just listings, and to PUSH each retailer until we're sure counts aren't
available. Shopify hides `inventory_quantity` in /products.json (only `available`), but the CART-ADD trick
forces the number: request a huge quantity and Shopify replies "Only N items were added … due to availability"
→ N is the exact on-hand count. Stores that oversell (add the full 9999) genuinely expose no count — we record
that as `oversell` so a source is provably exhausted, not silently skipped.

This blows the hemp-retailer list WAY out: any Shopify hemp shop becomes an inventory source. Lands
`hemp_inventory` as a dated time-series (retailer, product, variant, qty, method). Pair with Binny's (which
gives exact per-store qty natively) for the chain side. Polite, stdlib.

    python hemp_inventory.py --base https://www.nothingbuthemp.net
    python hemp_inventory.py            # all known hemp Shopify bases (orlando_hemp + offprem)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
