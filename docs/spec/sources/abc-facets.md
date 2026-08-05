# ABC FW&S (facets) — `abc-facets`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `abc-facets` |
| Runs | `import abc_facets as m; m.pull(cap=None)` |
| Module | `unifyd/abc_facets.py` — 130 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** SearchSpring


## 2. Transport

| constant | value |
|---|---|
| `API` | `https://%s.a.searchspring.io/api/search/search.json?siteId=%s&resultsFormat=native&resultsPerPage=%d&page=%d&q=&bgfilter.categories_hierarchy=%s` |


**Depends on** `warehouse`


## 3. What it lands


### `abc_products`

9,399 rows · 21 columns


| column | type |
|---|---|
| `sku` | `VARCHAR` |
| `uid` | `VARCHAR` |
| `name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `url` | `VARCHAR` |
| `category` | `VARCHAR` |
| `type` | `VARCHAR` |
| `varietal` | `VARCHAR` |
| `region` | `VARCHAR` |
| `country` | `VARCHAR` |
| `class` | `VARCHAR` |
| `size` | `VARCHAR` |
| `price` | `VARCHAR` |
| `msrp` | `VARCHAR` |
| `rating` | `VARCHAR` |
| `rating_count` | `VARCHAR` |
| `in_stock` | `VARCHAR` |
| `on_sale` | `VARCHAR` |
| `source_certified` | `VARCHAR` |
| `image` | `VARCHAR` |
| `raw_json` | `VARCHAR` |


**Written by** `abc_facets.py:117` (write_parquet)


### `source_taxonomy`

10,825 rows · 3 columns


| column | type |
|---|---|
| `source` | `VARCHAR` |
| `axis` | `VARCHAR` |
| `value` | `VARCHAR` |


**Written by** `abc_facets.py:120` (write_accumulate)


## 4. `abc_facets.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
abc_facets.py — harvest ABC's OWN taxonomy via its SearchSpring API (BigCommerce + SearchSpring, siteId p16j4k).

The insight: a chain's faceted navigation IS a maintained controlled taxonomy. Instead of parsing each PDP or
guessing attributes from the product name, we read the retailer's faceted data. Every product carries
`categories_hierarchy` — its DRILL-PATH memberships (Wine>Shop By Varietal>Cabernet Sauvignon, Wine>Shop By
Type>Red Wine, region, country) — plus custom_class / size / brand / price / rating / per-store stock. We parse
the drill paths into category/type/varietal/region: AUTHORITATIVE (the retailer tagged it), self-updating
(re-crawl catches new facets), and it feeds the dictionaries + the declarative matcher without hand-mining.

This is the SearchSpring RECIPE — the same shape works for any SearchSpring retailer (just the siteId changes).
Lands `abc_products` (rich catalog) + accumulates `source_taxonomy` (the facet vocabularies per source/axis).

    python abc_facets.py            # full harvest (~14k products)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
