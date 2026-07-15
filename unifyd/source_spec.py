#!/usr/bin/env python3
"""source_spec.py — the RAW field inventory per source, kept SEPARATE from the structured/master schema.

`gen_provenance.py` says, at a summary level, WHAT each source provides. This says exactly WHICH raw fields a
source emits — verbatim field names, what each means, and which structured/master field we map it into (or
whether it's DROPPED, i.e. kept only in raw_json, never promoted). Two reasons this is its own artifact:

  1. **Catch collapsed signal.** The raw list is where a source quietly loses fidelity before we even map it —
     the canonical case is Kroger, whose public API collapses a numeric on-hand count to HIGH/LOW/OOS. A raw
     field inventory makes "is there a truer field we're not seeing?" answerable per source.
  2. **Never silently drop a field.** Every raw field is accounted for: mapped, or explicitly DROPPED with a
     reason. New source fields that appear on a re-pull show up as un-catalogued → a prompt to decide.

Keep this current with every scraper change (see the provenance directive). `raw` field names are EXACTLY as
the source emits them (dotted = nested). `maps_to` = the structured field; "" = raw_json only (not promoted);
"DROP:<why>" = deliberately excluded. Emit `source_fields.json` for the MDM page + a workbook sheet.

    python source_spec.py            # print a coverage summary
    python source_spec.py --json     # emit source_fields.json
"""
import argparse
import json
import os
import sys

# Each entry: source key -> {"label","endpoint","grain","raw":[(field, meaning, maps_to), ...],"notes"}
# maps_to conventions: a structured field name, "" (raw_json only), or "DROP:<reason>".
SPEC = {
    # ── 7-Eleven / 7NOW — first-party BFF. EXACT per-store count is present RAW (store_quantity); the ORDER
    # cap (availableQuantity ≤100) is a separate, collapsed field. Both captured. (observed 2026-07-15) ──
    "sevennow": {
        "label": "7-Eleven (7NOW)",
        "endpoint": "GET /api/bff/unified-catalog/v1/conv/inventory/subcategories?storeId&categoryId",
        "grain": "product × store",
        "raw": [
            ("store_quantity", "TRUE on-hand unit count (uncapped)", "qty / store_quantity"),
            ("availableQuantity", "ORDERABLE cap (min(store_quantity−buffer, 100)) — collapsed", "available_quantity"),
            ("available", "in-stock bool", "available / in_stock"),
            ("availabilityMessage", "'Available' etc.", "stock_level"),
            ("minimum_on_hand_quantity", "reorder floor", ""),
            ("limit_per_order", "per-order max", ""),
            ("current_order_quantity", "qty already in this cart", "DROP:session-specific"),
            ("ignore_quantity", "sell-without-stock flag", ""),
            ("upc", "14-digit UPC", "upc"),
            ("slin", "7-Eleven internal SKU", "slin"),
            ("product_id", "catalog product id (e.g. 175730-0-1)", "product_id"),
            ("name", "product name", "name"),
            ("brand", "brand", "brand"),
            ("size_value", "size (e.g. 24oz)", "size"),
            ("price", "price in cents", "price (÷100)"),
            ("original_price", "pre-deal price in cents", "original_price"),
            ("promos", "array of deals {promo_short_desc,promo_long_desc,expiration_date,promo_type}", "on_promo/promo/promo_desc/promo_ends"),
            ("age_restricted", "alcohol/tobacco flag", "age_restricted"),
            ("age_restriction", "min age", ""),
            ("category", "department name", "category"),
            ("category_id", "department id", "department_id"),
            ("subcategory", "subcategory", "subcategory"),
            ("department_id", "department id", ""),
            ("images", "image URLs", "image"),
            ("thumbnail", "thumbnail URL", "image (fallback)"),
            ("long_desc", "description", "long_desc"),
            ("calories", "calories", "DROP:not bev-alc relevant"),
            ("country", "country", "DROP:always US"),
            ("tags", "merch tags", "raw_json"),
            ("meta_tags", "SEO tags", "DROP:SEO"),
            ("matching_ids", "cross-catalog ids", "raw_json"),
            ("matching_slins", "cross-catalog slins", "raw_json"),
            ("has_modifiers", "configurable flag", "DROP:food only"),
            ("isComboEligible", "combo-deal flag", "raw_json"),
            ("isFoodStampAllowed", "EBT flag", "DROP:not relevant"),
            ("popularity", "rank", "raw_json"),
            ("catalog_type", "catalog id (7now)", "DROP:constant"),
            ("consent_required", "age-gate flag", "raw_json"),
            ("bundle_promo_id", "bundle id", "raw_json"),
            ("nudge_description", "upsell text", "DROP:marketing"),
        ],
        "store_summary": [("inventory.active", "# active products at store", "store metric"),
                          ("inventory.outOfStock", "# OOS products at store", "store metric")],
        "notes": "store_summary from content/homepage .inventory. store_quantity is the whole point — a real "
                 "count where most chains give in/out only.",
    },
    # ── Haskell's — BigCommerce Storefront GraphQL (public JWT). Real count = availableToSell. (2026-07-15) ──
    "haskells": {
        "label": "Haskell's (BigCommerce)",
        "endpoint": "POST /graphql  site.route(path).node ...Product",
        "grain": "product (single store)",
        "raw": [
            ("entityId", "BigCommerce product id", "product_id"),
            ("name", "product name", "name"),
            ("sku", "SKU", "sku"),
            ("brand.name", "brand", "brand"),
            ("prices.price.value", "current price", "price"),
            ("prices.retailPrice.value", "list/retail price", "retail_price"),
            ("prices.salePrice.value", "sale price (if on sale)", "price + on_sale"),
            ("variants[].sku", "variant SKU", "sku"),
            ("variants[].gtin", "UPC/GTIN", "upc"),
            ("variants[].upc", "UPC (alt field)", "upc (fallback)"),
            ("variants[].inventory.isInStock", "in-stock bool", "in_stock"),
            ("variants[].inventory.aggregated.availableToSell", "EXACT on-hand units", "qty"),
            ("defaultImage.url", "image", "image"),
        ],
        "notes": "Whole catalog via sitemap (sidesteps the pagination-100 cap). Storefront JWT scraped from the "
                 "product page. Category from the page breadcrumb (not GraphQL here).",
    },
    # ── City Hive — SEO surface (widget product API is session-walled). No count via SEO. (2026-07-15) ──
    "cityhive": {
        "label": "City Hive (SEO surface)",
        "endpoint": "GET product page — JSON-LD Product + OpenGraph meta",
        "grain": "product × store",
        "raw": [
            ("ld:name", "product name (JSON-LD)", "name"),
            ("ld:brand.name", "brand", "brand"),
            ("ld:sku", "SKU", "sku"),
            ("ld:gtin13|gtin12|gtin", "UPC/GTIN", "upc"),
            ("ld:category", "category", "category"),
            ("ld:description", "description", "description"),
            ("ld:offers.price", "price (JSON-LD)", "price (fallback)"),
            ("ld:image", "image", "image"),
            ("og:title", "'Name SIZE - Store'", "name/size_ml"),
            ("product:price:amount", "price (OpenGraph)", "price"),
            ("og:description", "'Buy NAME size for $X from City - Store #NN in City, ST'", "store/store_loc/size"),
            ("ch:product:id", "City Hive product id", "pid/sku (fallback)"),
            ("og:image", "image", "image (fallback)"),
            ("url:option-id", "option id (from product URL)", "option_id"),
        ],
        "walled": [("product_options[].quantity", "per-store on-hand count", "NOT AVAILABLE via SEO — widget API session-walled"),
                   ("product_options[].max_purchase_quantity", "order cap", "widget API only"),
                   ("product_option/<id>/prices.json", "per-store price (public, needs store ctx)", "not yet wired"),
                   ("product/<id>/offers.json?option_id", "promos (public)", "not yet wired")],
        "notes": "Full catalog + price from SEO, no browser. Per-store count needs the session-walled widget "
                 "API (see [[cityhive-crack]]). prices.json/offers.json are public but need store/option context.",
    },
    # ── Kroger ATLAS (internal API) — the RICH per-GTIN payload (kroger_atlas.py). No raw count (HIGH/LOW),
    # but a master + enrichment trove: bottle dimensions, gtin14, ABV, taxonomy, planogram. (captured 2026-07-15) ──
    "kroger_atlas": {
        "label": "Kroger atlas (internal)",
        "endpoint": "GET /atlas/v1/product/v2/products?filter.gtin13s=…&projections=items.full,… (x-laf-object hdr)",
        "grain": "product × store",
        "raw": [
            ("item.gtin14", "14-digit GTIN", "gtin14"),
            ("item.upc", "UPC", "upc"),
            ("item.description", "name", "name"),
            ("item.brand.{name,code}", "brand + Kroger brand code", "brand / brand_code"),
            ("item.customerFacingSize", "size", "size"),
            ("item.dimensions.{height,width,length}", "PHYSICAL dims in inches [in_i] → mm (bottle: width≈length=Ø)",
             "height_mm / width_mm / length_mm / diameter_mm"),
            ("item.romanceDescription", "marketing HTML — carries ABV ('8.5% alcohol by volume')", "abv (parsed)"),
            ("item.familyTree.{commodity,department,subCommodity}", "Kroger taxonomy codes+names",
             "commodity/department/subcommodity (+codes)"),
            ("item.categories[]", "category", "category"),
            ("item.alcoholFlag / ageRestrictionFlag", "bev-alc + age flags", "alcohol_flag / age_restricted"),
            ("item.temperatureIndicatorName", "Ambient/Refrigerated/Frozen", "temperature"),
            ("item.snapEligible", "EBT", "snap_eligible"),
            ("item.prop65.required", "CA Prop 65", "prop65"),
            ("item.restrictionGroupCodes[]", "ship/sale restriction codes", "restriction_codes"),
            ("item.ratingsAndReviewsAggregate", "avg rating + review count", "avg_rating / num_reviews"),
            ("item.images[]", "images (front)", "image"),
            ("item.linkedItem", "related SKU ids", "raw_json"),
            ("item.omniChannelBrandName / receiptDescription / seoDescription", "alt names", "raw_json"),
            ("item.taxGroupCode / itemTypeCode / salesChannelCode", "internal codes", "raw_json"),
            ("location.locations[]", "PLANOGRAM — aisle desc/number/side, bayInAisle, numOfFacings (MULTIPLE bays)",
             "aisle / aisle_number / facings / planogram_json"),
            ("fulfillmentSummaries[].availability", "inventoryLevel HIGH/LOW + sellable, per modality",
             "pickup_level / delivery_level / instore_level"),
            ("price.storePrices.{regular,promo}", "price + sale + expiration", "price / sale_price / sale_ends"),
            ("laf[].modality.handoffLocation.{storeId,facilityId}", "store + facility", "store_id / facility_id"),
            ("sourceLocations[].dsdItem", "Direct Store Delivery flag (supplier delivers direct)", "dsd_item"),
            ("inventorySummaries[]", "empty in sampling — no numeric count", "DROP:empty/no count"),
        ],
        "notes": "kroger_atlas.py. The dimensions+gtin14 feed bottle_dims/master keyed by GTIN (authoritative + "
                 "free vs vision-derived); ABV fills a TTB gap; familyTree feeds the dictionary; planogram feeds "
                 "[[planogram-app]]. Needs a warmed kroger.com cookie + LAF header (store from DD_modStore).",
    },
    # ── Kroger PUBLIC API — THE motivating case: the public Developer API COLLAPSES a numeric count to a 3-value enum.
    # Raw internal count (if any) is UNCONFIRMED — needs a network trace on the site/app (see notes). ──
    "kroger": {
        "label": "Kroger (+ banners)",
        "endpoint": "GET /v1/products (official OAuth2 Developer API)",
        "grain": "product × store",
        "raw": [
            ("productId", "Kroger product id", "product_id"),
            ("upc", "UPC", "upc"),
            ("brand", "brand", "brand"),
            ("description", "product name/description", "name"),
            ("categories[]", "category list", "category"),
            ("items[].itemId", "item id", "sku"),
            ("items[].size", "size", "size"),
            ("items[].price.regular", "regular price", "price"),
            ("items[].price.promo", "promo price", "promo"),
            ("items[].inventory.stockLevel", "HIGH | LOW | TEMPORARILY_OUT_OF_STOCK — COLLAPSED count", "stock_level"),
            ("items[].fulfillment", "curbside/delivery/inStore/shipToHome bools", ""),
            ("aisleLocations[]", "aisle/shelf", "raw_json"),
            ("images[]", "images", "image"),
        ],
        "collapsed": [("items[].inventory.stockLevel", "public API inventory = a 3-value enum (HIGH/LOW/OOS), "
                       "NOT a number. RESOLVED 2026-07-15: the INTERNAL atlas API also collapses to the same "
                       "enum (availability.inventoryLevel HIGH/LOW) — there is NO raw on-hand count in Kroger's "
                       "web/app payload at all. The count is scrubbed at the source, not just the public layer.")],
        "internal": [
            ("endpoint", "GET /atlas/v1/product/v2/products?filter.gtin13s=…&projections=items.full,inventory.projected,…"
             " (the 'atlas' gateway the site/app uses; auth = x-laf-object header, LAF = [{assortmentKeys:[…], "
             "listingKeys:[<storeId>], …}] — NOT {banner,storeId,modality}, which is why hand-built replays 400'd)."),
            ("fulfillmentSummaries[].availability", "inventoryLevel (HIGH/LOW) + sellable + unavailabilityMessage, "
             "PER fulfillment type (PICKUP/DELIVERY/IN_STORE separately) — richer than the public single status, "
             "but STILL the enum, no number."),
            ("inventorySummaries[]", "present but EMPTY for the sampled SKU — the only remaining numeric lead; "
             "spot-check known-LOW SKUs, low expectation."),
            ("location.locations[] (item)", "PLANOGRAM — aisle #/side, bayInAisle, numOfFacings, physicalShelfNumber. "
             "Public API has NONE of this. The real reason to pull the internal API. Feeds [[planogram-app]]."),
            ("price regular/sale", "sale price + expirationDate + equivalizedUnitPrice — richer than public."),
        ],
        "notes": "RESOLVED: no raw numeric count anywhere in Kroger web/app (internal API also gives inventoryLevel "
                 "HIGH/LOW). Internal atlas API is still worth pulling for planogram + per-modality availability + "
                 "richer pricing. A true count would need a side channel (Instacart/Shipt 'only N left') or "
                 "Kroger's internal ordering systems — out of web scope.",
    },
}


def coverage(spec=None):
    spec = spec or SPEC
    out = {}
    for k, v in spec.items():
        raw = v.get("raw", [])
        mapped = sum(1 for _, _, m in raw if m and not m.startswith("DROP"))
        dropped = sum(1 for _, _, m in raw if m.startswith("DROP"))
        rawonly = sum(1 for _, _, m in raw if m == "" or m == "raw_json")
        out[k] = {"raw_fields": len(raw), "mapped": mapped, "raw_json_only": rawonly, "dropped": dropped,
                  "collapsed": len(v.get("collapsed", [])), "walled": len(v.get("walled", []))}
    return out


def emit_json(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "source_fields.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {k: {"label": v["label"], "endpoint": v["endpoint"], "grain": v.get("grain", ""),
               "raw_fields": [{"field": f, "meaning": m, "maps_to": t} for f, m, t in v.get("raw", [])],
               "collapsed": v.get("collapsed", []), "walled": v.get("walled", []),
               "internal": v.get("internal", []),
               "store_summary": v.get("store_summary", []), "notes": v.get("notes", "")}
           for k, v in SPEC.items()}
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Raw source-field spec (separate from the structured master schema).")
    ap.add_argument("--json", action="store_true", help="emit out/source_fields.json")
    a = ap.parse_args(argv)
    if a.json:
        print("wrote", emit_json())
        return 0
    for k, c in coverage().items():
        extra = []
        if c["collapsed"]:
            extra.append("%d COLLAPSED" % c["collapsed"])
        if c["walled"]:
            extra.append("%d walled" % c["walled"])
        print("  %-12s %2d raw fields | %2d mapped, %2d raw_json, %2d dropped%s"
              % (k, c["raw_fields"], c["mapped"], c["raw_json_only"], c["dropped"],
                 ("  [" + ", ".join(extra) + "]") if extra else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
