# NAOP on-premise — `naop`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `naop` |
| Runs | `import doordash_naop as m; m.run()` |
| Module | `unifyd/doordash_naop.py` — 208 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / 7200 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** DoorDash on-premise menus, $0 (ISP pool); consumes doordash_stores in NAOP_LIMIT batches


## 2. Transport

_No literal endpoint constant in `doordash_naop.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `cocktail_taxonomy`, `cuisine`, `doordash`, `observe`, `outlet_ident`, `runlog`, `warehouse`


## 3. What it lands


### `naop_accounts`

4,794 rows · 13 columns


| column | type |
|---|---|
| `store` | `VARCHAR` |
| `account` | `VARCHAR` |
| `clean_name` | `VARCHAR` |
| `street` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `phone` | `VARCHAR` |
| `cuisine` | `VARCHAR` |
| `cuisines` | `VARCHAR` |
| `cuisine_source` | `VARCHAR` |
| `serves_alcohol` | `BOOLEAN` |
| `n_beverages` | `BIGINT` |
| `run_id` | `VARCHAR` |


**Written by** `doordash_naop.py:195` (write_parquet)


### `naop_beverages`

7,139 rows · 16 columns


| column | type |
|---|---|
| `store` | `VARCHAR` |
| `account` | `VARCHAR` |
| `cuisine` | `VARCHAR` |
| `cuisines` | `VARCHAR` |
| `name` | `VARCHAR` |
| `description` | `VARCHAR` |
| `price` | `DOUBLE` |
| `price_basis` | `VARCHAR` |
| `category` | `VARCHAR` |
| `is_alcoholic` | `BOOLEAN` |
| `root` | `VARCHAR` |
| `sub` | `VARCHAR` |
| `base_spirit` | `VARCHAR` |
| `beer_style` | `VARCHAR` |
| `is_hemp` | `BOOLEAN` |
| `run_id` | `VARCHAR` |


**Written by** `doordash_naop.py:193` (write_parquet)


## 4. `doordash_naop.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
doordash_naop.py — on-premise (NAOP) beverage-alcohol from DoorDash RESTAURANT menus.

Restaurants carry per-item DESCRIPTIONS (cocktail components, the base brand poured) that retail lacks.
The menu is server-rendered as MenuPageItem objects (name, description, displayPrice) in the RSC payload —
Unlocker-fetchable, no browser, no account. We parse the whole menu, run every item through
cocktail_taxonomy.classify_beverage (which scans drinks AND desserts), keep the beverages (alcoholic +
mocktails), and land naop_beverages: account, item, category (cocktail/mocktail/beer/dessert), root/
sub-family/base_spirit or beer_style, and price — TAGGED delivery-inflated (restaurants mark up on DD).

    python doordash_naop.py --stores 122020        # Applebee's (Orlando)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
