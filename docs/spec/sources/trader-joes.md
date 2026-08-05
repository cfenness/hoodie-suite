# Trader Joe's — `trader-joes`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `trader-joes` |
| Runs | `import trader_joes as m; m.pull()` |
| Module | `unifyd/trader_joes.py` — 172 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** open storefront GraphQL + Brandify locator — no auth/anti-bot; SKU (no UPC), national pricing


## 2. Transport

| constant | value |
|---|---|
| `GQL` | `https://www.traderjoes.com/api/graphql` |
| `LOCATOR` | `https://alphaapi.brandify.com/rest/locatorsearch` |


**Depends on** `observe`, `warehouse`


## 3. What it lands


### `trader_joes_products`

4,024 rows · 10 columns


| column | type |
|---|---|
| `store_id` | `VARCHAR` |
| `sku` | `VARCHAR` |
| `name` | `VARCHAR` |
| `price` | `DOUBLE` |
| `retail_price` | `DOUBLE` |
| `size` | `DOUBLE` |
| `uom` | `VARCHAR` |
| `country_of_origin` | `VARCHAR` |
| `in_stock` | `BOOLEAN` |
| `source` | `VARCHAR` |


**Written by** `trader_joes.py:145` (write_accumulate)


## 4. `trader_joes.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
trader_joes.py — Trader Joe's bev-alc catalog via the OPEN storefront GraphQL (traderjoes.com/api/graphql).

No auth, no anti-bot: the `SearchProducts` query is public. Store list comes from TJ's Brandify locator
(public appkey). TJ is private-label so items carry a SKU (no UPC) + retail_price + size + country_of_origin
+ the "Wine, Beer & Liquor" category. Validated 2026-07-23: search "wine" @ store 768 → 767 wine items, 200.

We enumerate stores near a set of seed zips, then per store sweep the alcohol terms, page the result set,
keep only "Wine, Beer & Liquor" items, and land `trader_joes_products` (sku|store) + observations.
Stdlib-only (urllib); curl_cffi used if present. Note: not every TJ sells alcohol (state law) — those return
zero alcohol items, which is correct, not a failure.

    python trader_joes.py --stores 768 --terms wine,beer
```


## 5. Raw source fields

Endpoint: `POST /api/graphql  data.products.items[]` · grain: product (chain-level; no per-store)


| raw field | meaning | maps to |
|---|---|---|
| `sku` | TJ internal SKU (e.g. 083981) — NO UPC anywhere in the payload | `sku` |
| `item_title` | name | `name` |
| `price_range.minimum_price.final_price.value` | price | `price` |
| `retail_price` | list price (string) | `price (fallback)` |
| `availability` | '1'/'0' flag — in/out only, NO count | `in_stock` |
| `category_hierarchy[].name` | category tree (Products>Food>From The Freezer>…) | `category_path` |
| `sales_size / sales_uom_description` | size + unit ('7 Oz') | `size` |
| `primary_image / primary_image_meta` | image (+ srcset renditions) | `image` |
| `published / __typename` | publish flag / SimpleProduct\|ConfigurableProduct | `raw_json` |


Adobe Commerce GraphQL — same recipe shape as any Magento store. Food-first; the bev-alc slice is CA wine only (Charles Shaw). No UPC, no numeric count. Not yet built as a connector.
