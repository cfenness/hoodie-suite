#!/usr/bin/env python3
"""stop_and_shop.py — Stop & Shop bev-alc catalog via the Ahold/Peapod product API.

Stop & Shop runs on the **Ahold Delhaize / Peapod** digital platform (shared with Giant Food, Giant/Martin's,
Hannaford, Food Lion), whose product-search API returns a rich per-product record — `response.products[]` with
UPC, aisle+section (planogram-lite), category tree, nutrition, bottle-deposit, and price. We take EVERYTHING it
serves (+ raw_json). No numeric on-hand — availability is a `flags.outOfStock` boolean.

  ⚠️ ToS: stopandshop.com/robots.txt DISALLOWS /api/ (the data lives there) — this is a ToS-sensitive source.
     Treat like the ask-first sources ([[bevalc-enrichment]]): confirm the call before running a live pull.
     The site is also anti-bot (plain fetch → block page), so a live pull needs a warmed session / BD Browser.
     `parse_product` itself is ToS-neutral — it just flattens a payload you already have.

Endpoint (Peapod v6): `GET /api/v6.0/products/<store>?text=<term>&flags=true&nutrition=true&substitutions=false`
(store-scoped; the pasted response shape is `response.products[]` + `response.pagination`). Generalizes to the
other Ahold banners by swapping the host. Lands `stop_and_shop_products` + observe.

    python stop_and_shop.py --cookie "$SS_COOKIE" --store <storeId> --terms "wine,beer,seltzer"
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

# is_non_alc ships in the source-spec/non-alc PR; degrade gracefully until it merges so this is independently runnable
_is_non_alc = getattr(observe, "is_non_alc", lambda *a: False)

BASE = "https://stopandshop.com/api/v6.0/products"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
DEFAULT_TERMS = ["wine", "red wine", "white wine", "beer", "ipa", "hard seltzer", "cider", "sparkling wine",
                 "non-alcoholic beer", "champagne", "rose wine", "sangria", "malt beverage"]

FIELDS = ["prod_id", "upc", "name", "brand", "brand_id", "size", "unit_measure", "unit_price", "price",
          "regular_price", "on_sale", "root_cat", "root_cat_id", "subcat", "subcat_id", "category_path",
          "aisle", "section", "pick_location", "is_alcohol", "is_non_alc", "is_hemp", "out_of_stock",
          "has_coupon", "bottle_deposit", "calories", "serving_size", "servings", "ebt_eligible",
          "marketplace", "image", "review_id", "store_id", "captured_at", "source", "raw_json"]


def _img(im):
    if isinstance(im, dict):
        return im.get("medium") or im.get("large") or im.get("small") or ""
    return im or ""


def parse_product(p, store_id=""):
    """One Peapod product → a flat record. Captures everything the API serves; raw_json keeps the whole thing."""
    dep = p.get("bottleDepositMap") or {}
    nut = p.get("nutrition") or {}
    flags = p.get("flags") or {}
    name = p.get("name") or ""
    cat_path = " > ".join(str(c) for c in (p.get("categoryPath") or []))
    return {
        "prod_id": str(p.get("prodId") or ""), "upc": str(p.get("upc") or ""),
        "name": name, "brand": p.get("brand") or "", "brand_id": str(p.get("brandId") or ""),
        "size": p.get("size") or "", "unit_measure": p.get("unitMeasure") or "",
        "unit_price": p.get("unitPrice"), "price": p.get("price"),
        "regular_price": p.get("regularPrice"),
        "on_sale": bool(p.get("advertiseOnSale") or (p.get("price") is not None and p.get("regularPrice")
                        and p["price"] < p["regularPrice"])),
        "root_cat": p.get("rootCatName") or "", "root_cat_id": str(p.get("rootCatId") or ""),
        "subcat": p.get("subcatName") or "", "subcat_id": str(p.get("subcatId") or ""),
        "category_path": cat_path,
        "aisle": p.get("aisle") or "", "section": p.get("section") or "",
        "pick_location": p.get("pickStoreLocationId") or "",           # planogram: '09B-026-001-002'
        "is_alcohol": bool(p.get("isAlcohol")),
        "is_non_alc": _is_non_alc(name, p.get("subcatName") or "", p.get("brand") or ""),
        "is_hemp": observe.is_hemp(name, p.get("subcatName") or ""),
        "out_of_stock": bool(flags.get("outOfStock")),
        "has_coupon": bool(p.get("hasCoupon")),
        "bottle_deposit": json.dumps(dep) if dep else "",
        "calories": nut.get("totalCalories"), "serving_size": nut.get("servingSize") or "",
        "servings": nut.get("servingsPerContainer") or "",
        "ebt_eligible": bool(p.get("ebtEligible")), "marketplace": bool(p.get("isMarketplaceProduct")),
        "image": _img(p.get("image")), "review_id": p.get("reviewId") or "",
        "store_id": str(store_id), "captured_at": int(time.time()), "source": "stop_and_shop",
        "raw_json": json.dumps(p, separators=(",", ":"))[:6000],
    }


def fetch(term, store_id, cookie, start=0, rows=60, timeout=25):
    q = urllib.parse.urlencode({"text": term, "start": start, "rows": rows, "flags": "true",
                                "nutrition": "true", "substitutions": "false"})
    url = "%s/%s?%s" % (BASE, store_id, q)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Cookie": cookie, "Referer": "https://stopandshop.com/"})
    body = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    resp = (json.loads(body).get("response")) or {}
    return resp.get("products") or [], (resp.get("pagination") or {}).get("total", 0)


def run(terms, store_id, cookie, land=True, log=print):
    if not (cookie and store_id):
        log("[stop_and_shop] need --cookie + --store (warm a stopandshop.com session; robots DISALLOWS /api/ — "
            "confirm the ToS call before a live pull). parse_product works standalone on a payload you have.")
        return []
    seen, recs = set(), []
    for term in terms:
        start = 0
        while True:
            try:
                prods, total = fetch(term, store_id, cookie, start=start)
            except Exception as e:
                log("  %s @%d: %s" % (term, start, str(e)[:70])); break
            if not prods:
                break
            for p in prods:
                pid = str(p.get("prodId") or "")
                if pid and pid not in seen and p.get("isAlcohol"):
                    seen.add(pid); recs.append(parse_product(p, store_id))
            start += len(prods)
            log("  %-20s +%d (total for term ~%s)" % (term, len(prods), total))
            if start >= min(int(total or 0), 600):
                break
            time.sleep(0.6)
    if land and recs:
        warehouse.write_accumulate("stop_and_shop_products", recs, key=lambda r: r["prod_id"], fields=FIELDS)
        observe.record("stop_and_shop", [{"store": "Stop & Shop %s" % r["store_id"], "store_id": r["store_id"],
                                          "product_id": r["prod_id"], "upc": r["upc"], "brand": r["brand"],
                                          "name": r["name"], "price": r["price"], "on_promo": r["on_sale"],
                                          "in_stock": not r["out_of_stock"], "stock_level": "",
                                          "is_hemp": r["is_hemp"]} for r in recs], log=log)
    na = sum(1 for r in recs if r["is_non_alc"])
    log("[stop_and_shop] %d bev-alc products -> stop_and_shop_products (%d non-alc alternatives)" % (len(recs), na))
    return recs


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stop & Shop (Ahold/Peapod) bev-alc catalog.")
    ap.add_argument("--cookie", default=os.environ.get("SS_COOKIE", ""))
    ap.add_argument("--store", default=os.environ.get("SS_STORE", ""))
    ap.add_argument("--terms", default=",".join(DEFAULT_TERMS))
    a = ap.parse_args(argv)
    recs = run([t.strip() for t in a.terms.split(",") if t.strip()], a.store, a.cookie)
    for r in recs[:25]:
        print("  %-40s $%-6s %-14s aisle %s %s" % ((r["name"] or "")[:40], r["price"], r["subcat"],
                                                   r["aisle"], "NA" if r["is_non_alc"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
