#!/usr/bin/env python3
"""kroger_atlas.py — Kroger's INTERNAL 'atlas' product API: the rich per-GTIN payload the site/app uses.

The public Developer API (kroger_api.py) is thin. The internal atlas endpoint
`GET /atlas/v1/product/v2/products?filter.gtin13s=…&projections=items.full,…` returns FAR more per product —
a trove of MASTER + ENRICHMENT data keyed by GTIN — AND, when the response carries the populated inventory
objects, an EXACT per-store on-hand count (`inventorySummaries[].details[].availableToSell` /
`inventory.locations[].available`; some responses have it empty + only the HIGH/LOW enum):

  • dimensions {height,width,length} + gtin14  → BOTTLE DIMENSIONS keyed by GTIN (feeds bottle_dims/master —
    authoritative + free, vs the vision/label derivation). For a bottle width==length==diameter.
  • romanceDescription                          → ABV ("8.5% alcohol by volume") — a field TTB detail lacks.
  • familyTree / taxonomies                     → Kroger commodity/department/subCommodity codes (dictionary).
  • location.locations[]                        → planogram: aisle + numOfFacings (multiple bays), feeds planogram.
  • per-modality availability (PICKUP/DELIVERY/IN_STORE): inventoryLevel HIGH/LOW + sellable + pricing.
  • dsdItem (Direct Store Delivery), brand+code, prop65, restrictionGroupCodes, ratings.

ACCESS: gated by the `x-laf-object` request header. The shape (reverse-engineered from a real response) is an
array whose modality carries the store: `[{"listingKeys":["<storeId>"],"modality":{"type":"PICKUP",
"handoffLocation":{"facilityId":"<fid>","storeId":"<storeId>"}}}]` — the server reads
`laf[0].modality.handoffLocation.storeId`, which is why {banner,storeId,modality} replays 400'd. Needs a warmed
session cookie (Kroger is anti-bot + our Claude-in-Chrome tools are domain-blocked). Provide via
`--cookie`/`KROGER_COOKIE` + `--store`/`--facility` (from the DD_modStore cookie / a store lookup).

Lands `kroger_atlas_products` (full snapshot + raw_json) and feeds the dimensions into the enrichment. stdlib.

    python kroger_atlas.py --cookie "$KROGER_COOKIE" --store 01100439 --facility 14732 --gtins 0008312001225
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

API = "https://www.kroger.com/atlas/v1/product/v2/products"
PROJECTIONS = "items.full,offers.compact,nutrition.label,inventory.projected,variantGroupings.compact"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

# every field we promote off the atlas item + wrapper (raw_json keeps the whole thing regardless)
FIELDS = ["gtin14", "upc", "product_id", "name", "brand", "brand_code", "size", "category",
          "commodity", "commodity_code", "department", "department_code", "subcommodity", "subcommodity_code",
          "height_mm", "width_mm", "length_mm", "diameter_mm", "abv", "alcohol_flag", "age_restricted",
          "dsd_item", "temperature", "snap_eligible", "prop65", "restriction_codes", "avg_rating", "num_reviews",
          "aisle", "aisle_number", "facings", "planogram_json", "store_id", "facility_id",
          "available_units", "pickup_level", "delivery_level", "instore_level", "price", "sale_price", "sale_ends",
          "image", "captured_at", "source", "raw_json"]

_IN = re.compile(r"([\d.]+)\s*\[in_i\]", re.I)                       # "10.78 [in_i]"
# ABV in romanceDescription: "8.5% alcohol by volume", or OCR-spaced "8 5% alcohol", or "13% ABV"
_ABV = re.compile(r"(\d{1,2})(?:[.\s](\d))?\s*%\s*(?:alcohol|abv|alc\.?\s*by\s*vol)", re.I)


def _mm(dim_val):
    m = _IN.search(str(dim_val or ""))
    return round(float(m.group(1)) * 25.4, 1) if m else None


def _abv(text):
    m = _ABV.search(text or "")
    if not m:
        return None
    whole, dec = m.group(1), m.group(2)
    try:
        v = float("%s.%s" % (whole, dec)) if dec else float(whole)
        return v if 0 < v <= 75 else None
    except ValueError:
        return None


def parse_atlas_product(p):
    """Flatten ONE atlas product (the `data.products[]` element) into a rich record. Captures everything
    valuable off item.* + the fulfillment/location/price wrappers; raw_json preserves the full object."""
    it = p.get("item") or {}
    dims = it.get("dimensions") or {}
    ft = it.get("familyTree") or {}
    com, dep, sub = ft.get("commodity") or {}, ft.get("department") or {}, ft.get("subCommodity") or {}
    brand = it.get("brand") or {}
    h_mm, w_mm, l_mm = _mm(dims.get("height")), _mm(dims.get("width")), _mm(dims.get("length"))
    # per-modality availability (inventoryLevel = HIGH/LOW enum)
    lvl = {}
    for fs in (p.get("fulfillmentSummaries") or []):
        av = fs.get("availability") or {}
        lvl[(fs.get("type") or "").upper()] = av.get("inventoryLevel") or ("OOS" if av.get("sellable") is False else "")
    # EXACT numeric on-hand — present when the response carries the populated inventory objects (NOT always;
    # some responses have inventorySummaries:[] and only the enum). Prefer inventorySummaries[].details[]
    # .availableToSell, fall back to inventory.locations[].available.
    units = None
    for sm in (p.get("inventorySummaries") or []):
        for det in (sm.get("details") or []):
            a = det.get("availableToSell")
            if isinstance(a, (int, float)):
                units = a if units is None else max(units, a)
    if units is None:
        for locn in ((p.get("inventory") or {}).get("locations") or []):
            a = locn.get("available")
            if isinstance(a, (int, float)):
                units = a if units is None else max(units, a)
    # planogram — may have MULTIPLE locations (main aisle + display); keep the primary + the full list
    locs = ((p.get("location") or {}).get("locations")) or []
    prim = locs[0] if locs else {}
    aisle = (prim.get("aisle") or {})
    # price (store-scoped)
    sp = ((p.get("price") or {}).get("storePrices")) or {}
    reg = (sp.get("regular") or {}).get("defaultDescription") or ""
    promo = sp.get("promo") or {}
    laf0 = (p.get("laf") or [{}])[0]
    hl = ((laf0.get("modality") or {}).get("handoffLocation")) or {}
    srcloc = (p.get("sourceLocations") or [{}])[0]              # Direct-Store-Delivery flag + shippable
    cats = it.get("categories") or []
    return {
        "gtin14": it.get("gtin14") or "", "upc": it.get("upc") or p.get("id") or "",
        "product_id": (((p.get("itemsV1") or {}).get("product") or {}).get("id")) or "",
        "name": it.get("description") or "", "brand": brand.get("name") or "", "brand_code": brand.get("code") or "",
        "size": it.get("customerFacingSize") or "",
        "category": ", ".join(c.get("name", "") for c in cats),
        "commodity": com.get("name") or "", "commodity_code": com.get("code") or "",
        "department": dep.get("name") or "", "department_code": dep.get("code") or "",
        "subcommodity": sub.get("name") or "", "subcommodity_code": sub.get("code") or "",
        "height_mm": h_mm, "width_mm": w_mm, "length_mm": l_mm,
        "diameter_mm": w_mm if (w_mm and l_mm and abs(w_mm - l_mm) < 3) else None,   # bottle: width≈length≈Ø
        "abv": _abv(it.get("romanceDescription")),
        "alcohol_flag": bool(it.get("alcoholFlag")), "age_restricted": bool(it.get("ageRestrictionFlag")),
        "dsd_item": srcloc.get("dsdItem"),   # Direct Store Delivery (supplier delivers direct)
        "temperature": it.get("temperatureIndicatorName") or "",
        "snap_eligible": bool(it.get("snapEligible")),
        "prop65": bool((it.get("prop65") or {}).get("required")),
        "restriction_codes": ",".join(r.get("code", "") for r in (it.get("restrictionGroupCodes") or [])),
        "avg_rating": (it.get("ratingsAndReviewsAggregate") or {}).get("averageRating"),
        "num_reviews": (it.get("ratingsAndReviewsAggregate") or {}).get("numberOfReviews"),
        "aisle": aisle.get("description") or "", "aisle_number": aisle.get("number") or "",
        "facings": prim.get("numOfFacings") or "",
        "planogram_json": json.dumps(locs, separators=(",", ":"))[:1500],
        "store_id": hl.get("storeId") or "", "facility_id": hl.get("facilityId") or "",
        "available_units": units,   # EXACT on-hand when the response carries populated inventory (else None)
        "pickup_level": lvl.get("PICKUP", ""), "delivery_level": lvl.get("DELIVERY", ""),
        "instore_level": lvl.get("IN_STORE", ""),
        "price": reg, "sale_price": promo.get("defaultDescription") or "",
        "sale_ends": ((promo.get("expirationDate") or {}).get("value")) or "",
        "image": _front_image(it), "captured_at": int(time.time()), "source": "kroger_atlas",
        "raw_json": json.dumps(p, separators=(",", ":"))[:8000],
    }


def _front_image(it):
    for im in (it.get("images") or []):
        if im.get("perspective") == "front":
            return im.get("url") or ""
    imgs = it.get("images") or []
    return (imgs[0].get("url") if imgs else "") or ""


def _laf_header(store_id, facility_id, modality="PICKUP"):
    """The x-laf-object shape the server accepts — modality carries the store (server reads
    laf[0].modality.handoffLocation.storeId)."""
    return json.dumps([{"listingKeys": [store_id],
                        "modality": {"type": modality,
                                     "handoffLocation": {"facilityId": facility_id, "storeId": store_id}}}])


def fetch(gtins, cookie, store_id, facility_id, modality="PICKUP", timeout=25):
    q = "&".join("filter.gtin13s=%s" % g for g in gtins)
    url = "%s?%s&filter.verified=true&projections=%s" % (API, q, urllib.parse.quote(PROJECTIONS))
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json", "Cookie": cookie,
        "x-laf-object": _laf_header(store_id, facility_id, modality),
        "Referer": "https://www.kroger.com/"})
    body = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    return ((json.loads(body).get("data") or {}).get("products")) or []


def run(gtins, cookie, store_id, facility_id, modality="PICKUP", land=True, log=print):
    if not (cookie and store_id and facility_id):
        log("[kroger_atlas] need --cookie + --store + --facility (warm a kroger.com session; storeId from the "
            "DD_modStore cookie, facilityId from the product laf.modality.handoffLocation.facilityId).")
        return []
    recs = []
    for i in range(0, len(gtins), 20):                          # the site batches ~20-30 gtins/call
        chunk = gtins[i:i + 20]
        try:
            prods = fetch(chunk, cookie, store_id, facility_id, modality)
        except Exception as e:
            log("  gtins %d-%d: %s" % (i, i + len(chunk), str(e)[:80])); time.sleep(1); continue
        for p in prods:
            r = parse_atlas_product(p)
            recs.append(r)
        log("  +%d products (%d/%d gtins)" % (len(prods), min(i + 20, len(gtins)), len(gtins)))
        time.sleep(0.6)
    if land and recs:
        warehouse.write_accumulate("kroger_atlas_products", recs, key=lambda r: r["gtin14"] or r["upc"],
                                   fields=FIELDS)
        obs = [{"store": "Kroger %s" % r["store_id"], "store_id": r["store_id"], "product_id": r["product_id"],
                "upc": r["upc"], "brand": r["brand"], "name": r["name"], "price": None,
                "in_stock": r["pickup_level"] not in ("", "OOS"), "qty": r["available_units"],
                "stock_level": r["pickup_level"], "is_hemp": observe.is_hemp(r["name"], r["category"])}
               for r in recs]
        observe.record("kroger_atlas", obs, log=log)
    dims = sum(1 for r in recs if r["height_mm"]); abv = sum(1 for r in recs if r["abv"])
    log("[kroger_atlas] %d products -> kroger_atlas_products (%d w/ dimensions, %d w/ ABV)"
        % (len(recs), dims, abv))
    return recs


def warm_cookie(log=print):
    """Warm a FRESH kroger.com session cookie via browser_warm — the automatic replacement for the manual
    `KROGER_COOKIE` paste (a pasted session cookie always goes stale; a warmed one is minted per run). Only
    the COOKIE is warmed: the store comes from the x-laf-object header (--store/--facility), so it isn't
    session-bound. The atlas token replays through urllib fine (it's not TLS-fingerprint-bound like PX), so
    the warmed Cookie header is a drop-in. Returns '' if no browser is available → caller keeps the env
    cookie. Runs in the cloud too: headless Chrome under Xvfb + an ISP proxy (BROWSER_PROXY) — see
    .github/workflows/warm-sources.yml."""
    try:
        import browser_warm
    except Exception as e:
        log("[kroger_atlas] browser_warm unavailable (%s) — using KROGER_COOKIE env" % str(e)[:60])
        return ""
    log("[kroger_atlas] warming a kroger.com session (no manual cookie needed)…")
    # channel='chrome' = the real installed Chrome (best trust); challenge_gone clears once the app shell is up.
    return browser_warm.warm_cookie("kroger.com", "https://www.kroger.com/",
                                    challenge_gone="!!document.querySelector('[data-testid],header,#content')",
                                    channel="chrome", log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Kroger internal atlas API — rich per-GTIN master + enrichment.")
    ap.add_argument("--cookie", default=os.environ.get("KROGER_COOKIE", ""))
    ap.add_argument("--store", default=os.environ.get("KROGER_STORE", ""), help="listingKey/storeId (e.g. 01100439)")
    ap.add_argument("--facility", default=os.environ.get("KROGER_FACILITY", ""), help="facilityId (e.g. 14732)")
    ap.add_argument("--modality", default="PICKUP")
    ap.add_argument("--gtins", default="", help="comma-separated GTIN13s (default: bev-alc UPCs from the warehouse)")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--warm", dest="warm", action="store_true", default=None,
                    help="warm a fresh cookie via browser_warm (default: auto when --cookie/KROGER_COOKIE is empty)")
    ap.add_argument("--no-warm", dest="warm", action="store_false", help="never warm; use only the configured cookie")
    a = ap.parse_args(argv)
    # WARM AS THE FIRST STEP: a session cookie is the thing that goes stale, so refresh it each run instead of
    # relying on a pasted one. Default: warm iff no cookie was supplied (env KROGER_WARM=0 hard-disables).
    warm = a.warm if a.warm is not None else (not a.cookie and os.environ.get("KROGER_WARM", "1") != "0")
    if warm:
        fresh = warm_cookie()
        if fresh:
            a.cookie = fresh
    gtins = [g.strip() for g in a.gtins.split(",") if g.strip()]
    if not gtins:                                              # default: our bev-alc UPC universe
        try:
            gtins = [r["upc"] for r in warehouse.query(
                "kroger_products", "SELECT DISTINCT upc FROM t WHERE upc <> '' LIMIT %d" % a.limit)]
        except Exception:
            gtins = []
    recs = run(gtins, a.cookie, a.store, a.facility, a.modality)
    for r in recs[:25]:
        print("  %-38s Ø%-5s H%-6s ABV%-5s %s" % ((r["name"] or "")[:38], r["diameter_mm"], r["height_mm"],
                                                  r["abv"], r["aisle"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
