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

2,160,806 rows · 16 columns


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


### `postmates_products`

3,190 rows · 47 columns


| column | type |
|---|---|
| `item_uuid` | `VARCHAR` |
| `product_uuid` | `VARCHAR` |
| `store_uuid` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `name` | `VARCHAR` |
| `section` | `VARCHAR` |
| `subsection` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `gtins` | `VARCHAR` |
| `price` | `DOUBLE` |
| `list_price` | `DOUBLE` |
| `on_promo` | `BOOLEAN` |
| `discount` | `DOUBLE` |
| `promo_text` | `VARCHAR` |
| `promo_tag` | `VARCHAR` |
| `promo_type` | `VARCHAR` |
| `promo_pct` | `DOUBLE` |
| `promo_flat` | `DOUBLE` |
| `promo_uuid` | `VARCHAR` |
| `in_stock` | `BOOLEAN` |
| `is_sold_out` | `BOOLEAN` |
| `suspend_reason` | `VARCHAR` |
| `suspend_until` | `VARCHAR` |
| `low_availability` | `VARCHAR` |
| `avail_state` | `VARCHAR` |
| `stock_label` | `VARCHAR` |
| `max_qty` | `BIGINT` |
| `min_qty` | `DOUBLE` |
| `increment_qty` | `DOUBLE` |
| `default_qty` | `BIGINT` |
| `sold_by` | `VARCHAR` |
| `priced_by` | `VARCHAR` |
| `is_alcohol` | `BOOLEAN` |
| `num_alcoholic` | `BIGINT` |
| `age_rule` | `VARCHAR` |
| `abv` | `DOUBLE` |
| `pack` | `BIGINT` |
| `item_size` | `VARCHAR` |
| `nutritional_info` | `VARCHAR` |
| `classifications` | `VARCHAR` |
| `dietary_labels` | `VARCHAR` |
| `endorsements` | `VARCHAR` |
| `description` | `VARCHAR` |
| `image` | `VARCHAR` |
| `image_count` | `BIGINT` |
| `zone` | `VARCHAR` |
| `raw_json` | `VARCHAR` |


## 4. Module documentation

**`json.py` has no module docstring.** Everywhere else in this engine the docstring carries the rebuild narrative — the measurements behind the constants, the failure modes, the reason for the shape. Without it this source is only as legible as its code.


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
