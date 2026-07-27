# Kroger — Scraper Handoff

> Kroger's **internal “atlas” product API** — the rich per-GTIN payload the site/app use, far deeper
> than the public developer API. Master data + bottle dimensions + ABV + Kroger's commodity taxonomy
> + planogram + per-modality availability, and an **exact per-store on-hand count when populated**.

| | |
|---|---|
| **Status** | Live |
| **Registry id** | `kroger` (atlas inventory); `kroger-api` = the thin public UPC seed |
| **Entrypoint** | `import kroger_atlas as m; m.main([])` |
| **Class / cadence** | `mac` (warmed cookie) / daily |
| **Lands to** | `kroger_atlas_products` + `retail_observations` |
| **Requires** | `KROGER_COOKIE`, `KROGER_STORE`, `KROGER_FACILITY` |
| **Inventory signal** | Exact on-hand (when the response carries populated inventory) + HIGH/LOW enum |
| **Key files** | `kroger_atlas.py` (live), `kroger_api.py` (public OAuth UPC seed) |

## What we can accomplish

Per-GTIN **master + enrichment** across every Kroger banner (Ralphs, Fred Meyer, King Soopers,
Fry's, Smith's — same atlas API): authoritative **bottle dimensions keyed by GTIN** (free, vs
vision/label derivation), **ABV** from `romanceDescription` (a field TTB detail lacks), Kroger's own
**commodity taxonomy** (dictionary gold), **shelf planogram** (aisle + facings, multiple bays), and
**exact per-store on-hand** when the inventory objects are populated. It's the richest single-call
enrichment source we have; feed the GTIN universe from the public API seed.

## Access & mechanism

`GET https://www.kroger.com/atlas/v1/product/v2/products?filter.gtin13s=…&projections=items.full,offers.compact,nutrition.label,inventory.projected,variantGroupings.compact`,
batched ~20 GTINs/call. **Browser-token-gated:** needs a warmed session cookie (Akamai sensor —
homepage loads via a residential IP + ~15 cookies; the atlas API 403s without them) **plus** the
`x-laf-object` header that carries the store. Warm the token on the headful runner, then replay
headless. `polite.py` provides Kroger-specific backoff + breaker (`KROGER_PACE` / `KROGER_BREAKER`).

## Levels of pull

1. **Seed the GTIN universe**
   `from `kroger_products` (public OAuth API) or `--gtins``
   The GTIN13 list to enrich (our bev-alc UPC universe by default). The public API is just the seed — it collapses inventory to HIGH/LOW/OOS.
2. **Atlas product fetch**
   `GET /atlas/v1/product/v2/products?filter.gtin13s=…&projections=items.full,offers.compact,nutrition.label,inventory.projected,variantGroupings.compact`
   One rich product per GTIN. The **projections** are the sub-levels: `items.full` (master + dimensions + ABV + taxonomy + planogram), `offers.compact` (price/promo), `inventory.projected` (per-modality level + exact on-hand), `nutrition.label`, `variantGroupings.compact`.

## Every field we land

*(Table `kroger_atlas_products`, keyed by GTIN.)*


**Identity**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `gtin14 / upc / product_id` | str | 14-digit GTIN, UPC, product id. |
| `name / brand / brand_code` | str | Description + brand. |
| `size` | str | customerFacingSize. |

**Taxonomy (Kroger's own commodity tree — dictionary gold)**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `category` | str | categories[] names joined. |
| `commodity / commodity_code` | str | familyTree.commodity. |
| `department / department_code` | str | familyTree.department. |
| `subcommodity / subcommodity_code` | str | familyTree.subCommodity. |

**Bottle dimensions — authoritative + free, keyed by GTIN**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `height_mm / width_mm / length_mm` | float | `dimensions.*` (“10.78 [in_i]” → mm). |
| `diameter_mm` | float | Set when width≈length (a bottle). |

**Attributes**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `abv` | float | Parsed from `romanceDescription` (“8.5% alcohol by volume”) — a field TTB detail lacks. |
| `alcohol_flag / age_restricted` | bool | item.alcoholFlag / ageRestrictionFlag. |
| `dsd_item` | bool | Direct Store Delivery. |
| `temperature / snap_eligible / prop65` | str/bool | Handling + eligibility flags. |
| `restriction_codes` | str | restrictionGroupCodes[]. |
| `avg_rating / num_reviews` | float/int | ratingsAndReviewsAggregate. |

**Planogram (may have multiple bays)**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `aisle / aisle_number` | str | Primary location. |
| `facings` | int | numOfFacings on the primary bay. |
| `planogram_json` | json str | The full locations[] list (main aisle + display). |

**Inventory (store-scoped) — EXACT on-hand when populated**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `store_id / facility_id` | str | From the `x-laf-object` header. |
| `available_units` | int | **Exact on-hand** from `inventorySummaries[].details[].availableToSell` (fallback `inventory.locations[].available`). Present ONLY when the response carries populated inventory — else None + only the HIGH/LOW enum. |
| `pickup_level / delivery_level / instore_level` | str | Per-modality inventoryLevel (HIGH/LOW/OOS). |

**Price / media / provenance**

| Field | Type | What it is / where it comes from |
|---|---|---|
| `price / sale_price / sale_ends` | str | storePrices regular/promo. |
| `image` | str | Front-perspective image. |
| `captured_at / source / raw_json` | int/str/json | Fetch time, “kroger_atlas”, full object (≤8k). |

## Sample record

> **REPRESENTATIVE** — real field names & types, illustrative values. A spirits GTIN. Dimensions/ABV/taxonomy shapes match real atlas responses.

```json
{
  "gtin14": "00008660000618",
  "upc": "008660000618",
  "product_id": "0000866000061",
  "name": "Buffalo Trace Kentucky Straight Bourbon Whiskey",
  "brand": "Buffalo Trace",
  "brand_code": "BFT",
  "size": "750 ml",
  "category": "Liquor, Bourbon",
  "commodity": "LIQUOR",
  "commodity_code": "27",
  "department": "ADULT BEVERAGE",
  "department_code": "09",
  "subcommodity": "BOURBON/TENNESSEE WHISKEY",
  "subcommodity_code": "2704",
  "height_mm": 273.7,
  "width_mm": 82.6,
  "length_mm": 82.6,
  "diameter_mm": 82.6,
  "abv": 45.0,
  "alcohol_flag": true,
  "age_restricted": true,
  "dsd_item": false,
  "temperature": "SHELF STABLE",
  "snap_eligible": false,
  "prop65": false,
  "restriction_codes": "ALC",
  "avg_rating": 4.8,
  "num_reviews": 512,
  "aisle": "Liquor",
  "aisle_number": "27",
  "facings": 4,
  "planogram_json": "[{\"aisle\":{\"description\":\"Liquor\",\"number\":\"27\"},\"numOfFacings\":4}]",
  "store_id": "01100439",
  "facility_id": "14732",
  "available_units": 37,
  "pickup_level": "HIGH",
  "delivery_level": "HIGH",
  "instore_level": "HIGH",
  "price": "$29.99",
  "sale_price": "$26.99",
  "sale_ends": "2026-07-31",
  "image": "https://www.kroger.com/product/images/large/front/0000866000061.jpg",
  "captured_at": 1753286400,
  "source": "kroger_atlas",
  "raw_json": "{...full atlas product...}"
}
```

## Gotchas & hard-won learnings

- **Use `kroger_atlas`, not `kroger_api`.** The public Developer API (`kroger_api.py`, OAuth) collapses inventory to HIGH/LOW/OOS and has none of the dimensions/planogram/taxonomy. It exists only to seed the GTIN universe. (This exact side-by-side confusion once caused the thin scraper to run instead of the real one — the archive rule exists because of it.)
- **The `x-laf-object` header shape is specific.** `[{listingKeys:[storeId], modality:{type:PICKUP, handoffLocation:{facilityId, storeId}}}]` — the server reads `laf[0].modality.handoffLocation.storeId`. A `{banner,storeId,modality}` shape 400s (why early headless replays failed).
- **Exact count is present only when `inventorySummaries[]` is populated.** Some responses have `inventorySummaries: []` and only the HIGH/LOW enum. A first empty sample led to a wrong “no count exists” conclusion; a later Ralphs sample with populated inventory shows `availableToSell`. Same atlas API on all banners.
- **Same code needs a warmed Akamai cookie.** Homepage via residential = 200 + cookies; atlas API 403 without the sensor cookie. This is why it's `klass=mac` (warm on the headful runner, replay).
- **Claude-in-Chrome tools are permission-blocked on kroger.com** (navigate/network/JS) — recon was done via captured payloads, not live browser tooling.
- **Kroger UPC shape (`checkless-13`):** `kroger_products.upc` is the UPC-A body with NO check digit, zero-padded to 13 (~90% fail EAN-13 as-is). `upc.from_checkless_13` heals it (drops the `00` pad, appends the computed check) — proven by cross-source join lift (0 → 1,347). Note the product master already handles this via `sku_match.norm_upc` core reduction, so don't add a per-source heal hook to the build.

## Maintenance & health

Single-endpoint + verify row-count. The failure mode to watch is a **cookie expiry** (atlas 403 →
0 rows = failed scrape, not a rebuild — the empty-write guard protects the catalog). The `available_units`
fill-rate is the signal for whether inventory objects are populated that run; the field-drift audit
(`deep_audit.py`) flags a column that goes null.

## Files & entrypoints

- `unifyd/kroger_atlas.py` — the live scraper: `parse_atlas_product()` (the 45-col schema), `fetch()`, `run()`, `_laf_header()`.
- `unifyd/kroger_api.py` — public OAuth API; seeds `kroger_products` (GTIN universe). Registry id `kroger-api`, weekly.
- Run: `python kroger_atlas.py --cookie "$KROGER_COOKIE" --store 01100439 --facility 14732 --gtins 0008312001225`


---
*Part of the Hoodie Suite scraper cutover pack. See [`README.md`](README.md) for the shared architecture, anti-bot infrastructure, and standing rules that apply to every source.*
