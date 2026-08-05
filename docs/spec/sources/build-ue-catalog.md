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


| column | type | filled |
|---|---|---|
| `store_uuid` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `source` | `INTEGER` | **0%** ‹never populated› |
| `item_uuid` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `INTEGER` | **0%** ‹never populated› |
| `upc` | `VARCHAR` | **3.1%** |
| `gtin` | `INTEGER` | **0%** ‹never populated› |
| `price` | `DOUBLE` | 100.0% |
| `list_price` | `DOUBLE` | **2.5%** |
| `promo` | `INTEGER` | **0%** ‹never populated› |
| `size` | `INTEGER` | **0%** ‹never populated› |
| `abv` | `DOUBLE` | **0.8%** |
| `in_stock` | `BOOLEAN` | 100.0% |
| `stock_label` | `VARCHAR` | 8.9% |
| `category` | `INTEGER` | **0%** ‹never populated› |
| `__b` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

> **6 columns never populated:** `source`, `brand`, `gtin`, `promo`, `size`, `category`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


### `postmates_products`

3,190 rows · 47 columns


| column | type | filled |
|---|---|---|
| `item_uuid` | `VARCHAR` | 100.0% |
| `product_uuid` | `VARCHAR` | 74.7% |
| `store_uuid` | `VARCHAR` | 100.0% |
| `store_name` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `section` | `VARCHAR` | 100.0% |
| `subsection` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 23.4% |
| `gtins` | `VARCHAR` | 23.4% |
| `price` | `DOUBLE` | 100.0% |
| `list_price` | `DOUBLE` | 36.4% |
| `on_promo` | `BOOLEAN` | 100.0% |
| `discount` | `DOUBLE` | 100.0% |
| `promo_text` | `VARCHAR` | 17.3% |
| `promo_tag` | `VARCHAR` | 17.3% |
| `promo_type` | `VARCHAR` | 17.6% |
| `promo_pct` | `DOUBLE` | **4.1%** |
| `promo_flat` | `DOUBLE` | **4.1%** |
| `promo_uuid` | `VARCHAR` | 17.6% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `is_sold_out` | `BOOLEAN` | 100.0% |
| `suspend_reason` | `VARCHAR` | 25.3% |
| `suspend_until` | `VARCHAR` | **0%** ‹never populated› |
| `low_availability` | `VARCHAR` | **0%** ‹never populated› |
| `avail_state` | `VARCHAR` | 74.7% |
| `stock_label` | `VARCHAR` | **2.2%** |
| `max_qty` | `BIGINT` | 100.0% |
| `min_qty` | `DOUBLE` | 100.0% |
| `increment_qty` | `DOUBLE` | 100.0% |
| `default_qty` | `BIGINT` | 100.0% |
| `sold_by` | `VARCHAR` | 100.0% |
| `priced_by` | `VARCHAR` | 100.0% |
| `is_alcohol` | `BOOLEAN` | 100.0% |
| `num_alcoholic` | `BIGINT` | 9.6% |
| `age_rule` | `VARCHAR` | **1.6%** |
| `abv` | `DOUBLE` | 6.8% |
| `pack` | `BIGINT` | 14.8% |
| `item_size` | `VARCHAR` | 72.4% |
| `nutritional_info` | `VARCHAR` | 33.7% |
| `classifications` | `VARCHAR` | **0%** ‹never populated› |
| `dietary_labels` | `VARCHAR` | 21.8% |
| `endorsements` | `VARCHAR` | 35.0% |
| `description` | `VARCHAR` | 25.3% |
| `image` | `VARCHAR` | 100.0% |
| `image_count` | `BIGINT` | 100.0% |
| `zone` | `VARCHAR` | 100.0% |
| `raw_json` | `VARCHAR` | **0%** ‹never populated› |

Fill measured over **full table** (3,190 rows).

> **4 columns never populated:** `suspend_until`, `low_availability`, `classifications`, `raw_json`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


## 4. Module documentation

**`json.py` has no module docstring.** Everywhere else in this engine the docstring carries the rebuild narrative — the measurements behind the constants, the failure modes, the reason for the shape. Without it this source is only as legible as its code.


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
