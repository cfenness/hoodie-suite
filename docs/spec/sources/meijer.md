# Meijer — `meijer`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `meijer` |
| Runs | `import meijer as m; m.pull()` |
| Module | `unifyd/meijer.py` — 179 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** open storefront GraphQL (digital.meijer.com) — no auth/anti-bot; per-store alcohol sweep


## 2. Transport

| constant | value |
|---|---|
| `GQL` | `https://digital.meijer.com/graphql/` |


**Depends on** `observe`, `warehouse`


## 3. What it lands


### `meijer_products`

2,144 rows · 16 columns


| column | type | filled |
|---|---|---|
| `store_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `size` | `DOUBLE` | 100.0% |
| `uom` | `VARCHAR` | **0%** ‹never populated› |
| `base_price` | `DOUBLE` | 100.0% |
| `price` | `DOUBLE` | 100.0% |
| `promo_price` | `DOUBLE` | 94.3% |
| `on_sale` | `BOOLEAN` | 100.0% |
| `price_text` | `VARCHAR` | 100.0% |
| `savings` | `VARCHAR` | 94.3% |
| `promo` | `VARCHAR` | **0%** ‹never populated› |
| `stock_status` | `VARCHAR` | 100.0% |
| `in_stock` | `BOOLEAN` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (2,144 rows).

> **2 columns never populated:** `uom`, `promo`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `meijer.py:151` (write_accumulate)


## 4. `meijer.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
meijer.py — Meijer bev-alc catalog via the OPEN storefront GraphQL (digital.meijer.com/graphql).

No auth, no cookie, no anti-bot: the `productSearch` query is public — POST with the header
`x-meijer-gql-query: search` and a storeId, and it returns per-store product records with UPC,
alcohol flag, price (base/customer/promo), and inventory stockStatus. Validated 2026-07-23:
`wine` @ store 265 → 2415 items, 200 OK, no token.

We crawl the alcohol terms per store, page through the cursor connection, keep only isAlcohol
items, and land `meijer_products` (keyed upc|storeId) + the retail_observations time-series.
Stdlib-only (urllib); curl_cffi (Chrome-JA3) is used when present but the endpoint is open either way.

    python meijer.py --stores 265 --terms wine,beer,spirits
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
