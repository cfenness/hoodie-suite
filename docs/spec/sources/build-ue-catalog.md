# UberEats/Postmates catalog fold (parts → catalog, incremental) — `build-ue-catalog`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-ue-catalog` |
| Runs | `import json, fold; rs=[fold.run(t) for t in ('ubereats_products', 'postmates_products')]; n=sum(r['rows'] for r in rs); p=sum(r['parts'] for r in rs); st='degraded' if any(r['status']=='degraded' for r in rs) else ('current' if all(r['status']=='current' for r in rs) else 'ok'); print('HOODIE_RESULT '+json.dumps({'status':st,'items_done':n,'items_total':n,'note':'%d parts folded' % p}))` |
| Module | `unifyd/json.py` |
| Cadence | every 6h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 8192 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** incremental single-writer fold (fold.py): watermarked, set-based, per-column merge. Shards append parts and must never merge (lost updates).


## 2. Transport

_No literal endpoint constant in `json.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


## 3. What it lands


### `ubereats_products`

2,160,806 rows · 17 columns


| column | type |
|---|---|
| `store_uuid` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `source` | `INTEGER` |
| `item_uuid` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `INTEGER` |
| `upc` | `VARCHAR` |
| `gtin` | `INTEGER` |
| `price` | `DOUBLE` |
| `list_price` | `DOUBLE` |
| `promo` | `INTEGER` |
| `size` | `INTEGER` |
| `abv` | `DOUBLE` |
| `in_stock` | `BOOLEAN` |
| `stock_label` | `VARCHAR` |
| `category` | `INTEGER` |
| `__b` | `VARCHAR` |


### `postmates_products`

_Not in the live capture — the code writes it but the table was not scanned._


## 4. Module documentation

**`json.py` has no module docstring.** Everywhere else in this engine the docstring carries the rebuild narrative — the measurements behind the constants, the failure modes, the reason for the shape. Without it this source is only as legible as its code.


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
