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


| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `account` | `VARCHAR` | 100.0% |
| `clean_name` | `VARCHAR` | 96.0% |
| `street` | `VARCHAR` | 95.9% |
| `city` | `VARCHAR` | 96.0% |
| `state` | `VARCHAR` | 96.0% |
| `phone` | `VARCHAR` | **0%** ‹never populated› |
| `cuisine` | `VARCHAR` | 73.7% |
| `cuisines` | `VARCHAR` | 65.4% |
| `cuisine_source` | `VARCHAR` | 100.0% |
| `serves_alcohol` | `BOOLEAN` | 100.0% |
| `n_beverages` | `BIGINT` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |

Fill measured over **full table** (4,794 rows).

> **1 column never populated:** `phone`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `doordash_naop.py:195` (write_parquet)


### `naop_beverages`

7,139 rows · 16 columns


| column | type | filled |
|---|---|---|
| `store` | `VARCHAR` | 100.0% |
| `account` | `VARCHAR` | 100.0% |
| `cuisine` | `VARCHAR` | 70.8% |
| `cuisines` | `VARCHAR` | 67.4% |
| `name` | `VARCHAR` | 100.0% |
| `description` | `VARCHAR` | 88.6% |
| `price` | `DOUBLE` | 96.6% |
| `price_basis` | `VARCHAR` | 100.0% |
| `category` | `VARCHAR` | 100.0% |
| `is_alcoholic` | `BOOLEAN` | 100.0% |
| `root` | `VARCHAR` | 26.2% |
| `sub` | `VARCHAR` | 44.2% |
| `base_spirit` | `VARCHAR` | 25.3% |
| `beer_style` | `VARCHAR` | 5.2% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `run_id` | `VARCHAR` | 100.0% |

Fill measured over **full table** (7,139 rows).

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
