# 7-Eleven (7NOW) — `sevennow`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `sevennow` |
| Runs | `import sevennow_warm as m; m.main()` |
| Module | `unifyd/sevennow_warm.py` — 44 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `mac` |
| Cost class | mac |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `patchright` |
| Unit test | **none** |


**Registry note.** Incapsula — patchright


## 2. Transport

_No literal endpoint constant in `sevennow_warm.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `browser_warm`, `kroger_api`, `sevennow`


## 3. What it lands


### `sevennow_products`

5,304 rows · 29 columns


| column | type | filled |
|---|---|---|
| `store_id` | `VARCHAR` | 100.0% |
| `store_city` | `VARCHAR` | 100.0% |
| `department` | `VARCHAR` | 100.0% |
| `department_id` | `VARCHAR` | 100.0% |
| `category` | `VARCHAR` | 100.0% |
| `subcategory` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `slin` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | **0%** ‹never populated› |
| `size` | `VARCHAR` | 100.0% |
| `price` | `DOUBLE` | 100.0% |
| `original_price` | `INTEGER` | **0%** ‹never populated› |
| `available` | `BOOLEAN` | 100.0% |
| `available_quantity` | `BIGINT` | 100.0% |
| `store_quantity` | `BIGINT` | 100.0% |
| `age_restricted` | `BOOLEAN` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `hemp_signal` | `VARCHAR` | **0%** ‹never populated› |
| `on_promo` | `BOOLEAN` | 100.0% |
| `promo` | `VARCHAR` | 33.0% |
| `promo_desc` | `VARCHAR` | 30.2% |
| `promo_ends` | `VARCHAR` | 33.0% |
| `image` | `VARCHAR` | 100.0% |
| `long_desc` | `VARCHAR` | 100.0% |
| `captured_at` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `raw_json` | `VARCHAR` | 100.0% |

Fill measured over **full table** (5,304 rows).

> **3 columns never populated:** `brand`, `original_price`, `hemp_signal`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `sevennow.py:224` (write_accumulate)


## 4. `sevennow_warm.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
sevennow_warm.py — pull 7NOW with NO BD and NO cookie hand-off. Incapsula's reese84 is TLS-fingerprint-bound
(a valid cookie replayed over urllib still 403s — same lesson as PerimeterX/Cloudflare), so we do the API calls
IN-PAGE through browser_warm (real Chrome, Mac residential IP): the fetch inherits the browser's trusted session
+ TLS. A BrowserFetcher (same .get(url)->json interface as sevennow.Fetcher) is monkeypatched into the built
scraper, so all its parsing/landing is reused. This is the durable Mac-side template (UberEats-style in-page).
```


## 5. Raw source fields

Endpoint: `GET /api/bff/unified-catalog/v1/conv/inventory/subcategories?storeId&categoryId` · grain: product × store


| raw field | meaning | maps to |
|---|---|---|
| `store_quantity` | TRUE on-hand unit count (uncapped) | `qty / store_quantity` |
| `availableQuantity` | ORDERABLE cap (min(store_quantity−buffer, 100)) — collapsed | `available_quantity` |
| `available` | in-stock bool | `available / in_stock` |
| `availabilityMessage` | 'Available' etc. | `stock_level` |
| `minimum_on_hand_quantity` | reorder floor | _raw_json only_ |
| `limit_per_order` | per-order max | _raw_json only_ |
| `current_order_quantity` | qty already in this cart | `DROP:session-specific` |
| `ignore_quantity` | sell-without-stock flag | _raw_json only_ |
| `upc` | 14-digit UPC | `upc` |
| `slin` | 7-Eleven internal SKU | `slin` |
| `product_id` | catalog product id (e.g. 175730-0-1) | `product_id` |
| `name` | product name | `name` |
| `brand` | brand | `brand` |
| `size_value` | size (e.g. 24oz) | `size` |
| `price` | price in cents | `price (÷100)` |
| `original_price` | pre-deal price in cents | `original_price` |
| `promos` | array of deals {promo_short_desc,promo_long_desc,expiration_date,promo_type} | `on_promo/promo/promo_desc/promo_ends` |
| `age_restricted` | alcohol/tobacco flag | `age_restricted` |
| `age_restriction` | min age | _raw_json only_ |
| `category` | department name | `category` |
| `category_id` | department id | `department_id` |
| `subcategory` | subcategory | `subcategory` |
| `department_id` | department id | _raw_json only_ |
| `images` | image URLs | `image` |
| `thumbnail` | thumbnail URL | `image (fallback)` |
| `long_desc` | description | `long_desc` |
| `calories` | calories | `DROP:not bev-alc relevant` |
| `country` | country | `DROP:always US` |
| `tags` | merch tags | `raw_json` |
| `meta_tags` | SEO tags | `DROP:SEO` |
| `matching_ids` | cross-catalog ids | `raw_json` |
| `matching_slins` | cross-catalog slins | `raw_json` |
| `has_modifiers` | configurable flag | `DROP:food only` |
| `isComboEligible` | combo-deal flag | `raw_json` |
| `isFoodStampAllowed` | EBT flag | `DROP:not relevant` |
| `popularity` | rank | `raw_json` |
| `catalog_type` | catalog id (7now) | `DROP:constant` |
| `consent_required` | age-gate flag | `raw_json` |
| `bundle_promo_id` | bundle id | `raw_json` |
| `nudge_description` | upsell text | `DROP:marketing` |


store_summary from content/homepage .inventory. store_quantity is the whole point — a real count where most chains give in/out only.
