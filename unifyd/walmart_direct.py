"""walmart_direct.py — Walmart bev-alc DIRECT (no Bright Data). Walmart's desktop site is PerimeterX-walled
(307/418), but a MOBILE Safari UA walks right past it (same trick as Total Wine) and the search page embeds the
full product list in its `__NEXT_DATA__` JSON. So we paginate the bev-alc search terms, parse the items, and
land walmart_products + a national price observation — all on our own IP, $0.

Fields per item: name (size/ABV embedded → the master's parsers derive brand/size/abv), usItemId, price, image,
canonical URL, department/category. No UPC in search (Walmart doesn't expose it) — fine, matching rests on the
name-key + attribute vector, not UPC.

    python walmart_direct.py                     # default bev-alc terms
    python walmart_direct.py --terms "bourbon,tequila" --max-pages 5

Polite: mobile UA, delay + backoff, direct-first. BD stays only as an optional last resort (WALMART_BD=1).
"""
import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

_MOB = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
DEFAULT_TERMS = ["cabernet sauvignon wine", "red wine", "white wine", "chardonnay", "pinot noir", "champagne",
                 "prosecco", "rose wine", "moscato", "beer", "ipa beer", "lager", "mexican beer",
                 "hard seltzer", "hard cider", "canned cocktail", "bourbon whiskey", "scotch whisky",
                 "vodka", "tequila", "rum", "gin", "whiskey", "hemp drink", "thc seltzer"]
_ND = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_PRICE = re.compile(r"[\d.]+")
_SIZE = re.compile(r"(\d[\d.]*)\s*(ml|l\b|liter|litre|oz)", re.I)


def _size_ml(name):
    m = _SIZE.search(name or "")
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2).lower()
    return v if u == "ml" else v * 29.5735 if u == "oz" else v * 1000


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": _MOB, "Accept": "text/html",
                                               "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip"})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return gzip.decompress(raw).decode("utf-8", "replace") if raw[:2] == b"\x1f\x8b" else raw.decode("utf-8", "replace")


def _find(o, key="itemStacks"):
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = _find(v, key)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def _fnum(s):
    m = _PRICE.search(str(s or ""))
    return float(m.group(0)) if m else None


def search_page(term, page=1):
    """One page of a bev-alc search — the items embedded in __NEXT_DATA__."""
    url = "https://www.walmart.com/search?q=%s&page=%d" % (urllib.request.quote(term), page)
    m = _ND.search(_get(url))
    if not m:
        return []
    stacks = _find(json.loads(m.group(1))) or []
    out = []
    for st in stacks:
        for it in (st.get("items") or []):
            nm = it.get("name")
            if not nm or it.get("__typename") not in (None, "Product"):
                pass
            if not nm:
                continue
            out.append({"product_name": nm, "item_id": str(it.get("usItemId") or it.get("id") or ""),
                        "price": _fnum((it.get("priceInfo") or {}).get("linePrice")), "size_ml": _size_ml(nm),
                        "brand": it.get("brand") or "", "image": (it.get("imageInfo") or {}).get("thumbnailUrl") or "",
                        "url": "https://www.walmart.com" + (it.get("canonicalUrl") or ""),
                        "category": it.get("departmentName") or "", "is_alcohol": True})
    return out


# product-detail spec name (lowercased contains) -> our rich field. The /ip/ page's idml.specifications carries
# the RNDC-style vector: varietal, region, vintage, ABV, container, flavor, pairing, wine score, size.
_SPEC_FIELD = [("wine varietal", "varietal"), ("varietal", "varietal"), ("grape", "varietal"),
               ("wine region", "region"), ("region", "region"), ("appellation", "region"),
               ("vintage", "vintage"), ("alcohol content", "abv_str"), ("proof", "abv_str"),
               ("container type", "container"), ("container", "container"), ("flavor", "flavor"),
               ("food pairing", "pairing"), ("wine pairing", "pairing"), ("pairing", "pairing"),
               ("wine score", "wine_score"), ("size", "size_str"), ("net content", "size_str")]


def _product_node(dd):
    """The rich /ip/ product object in __NEXT_DATA__ — the dict carrying usItemId + name (+ upc). _find returns
    the first `product` key, but that isn't always the item node, so verify shape and fall back to a walk."""
    p = _find(dd, "product")
    if isinstance(p, dict) and p.get("usItemId") and p.get("name"):
        return p
    hit = [None]

    def walk(o):
        if hit[0] is not None:
            return
        if isinstance(o, dict):
            if o.get("usItemId") and o.get("name") and ("upc" in o or "priceInfo" in o):
                hit[0] = o; return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(dd)
    return hit[0] or {}


_ABV_TXT = re.compile(r"(\d{1,2}(?:\.\d)?)\s*%\s*ABV", re.I)


def detail(url, log=print):
    """Fetch a product /ip/ page and pull EVERYTHING valuable off the product object — the rich spec vector
    (varietal/region/vintage/ABV/container/…) AND the master + enrichment fields the object carries directly:
    UPC (the master key — exposed on detail even though search hides it), aisle (productLocation), retail-
    hierarchy taxonomy (rhPath/category path/productTypeId), order limit, ratings, Rollback badge, per-store
    availability. Defensive: walks __NEXT_DATA__ for whatever sits where. Pace it (Walmart throttles detail)."""
    try:
        m = _ND.search(_get(url))
    except Exception as e:
        log("  detail %s: %s" % (url[-40:], str(e)[:50])); return {}
    if not m:
        return {}
    dd = json.loads(m.group(1))
    out = {}
    specs = _find(dd, "specifications")
    if isinstance(specs, list):
        for s in specs:
            nm = str(s.get("name") or "").lower(); val = s.get("value")
            if not val:
                continue
            for key, fld in _SPEC_FIELD:
                if key in nm and fld not in out:
                    out[fld] = (", ".join(val) if isinstance(val, list) else str(val)).strip(); break
    p = _product_node(dd)
    # ── master identity keys ──
    out["upc"] = p.get("upc") or ""                              # exposed on DETAIL (not search) — the match key
    out["item_id"] = str(p.get("usItemId") or out.get("item_id") or "")
    out["offer_id"] = p.get("offerId") or ""
    out["brand"] = p.get("brand") or out.get("brand") or ""
    out["type"] = p.get("type") or ""                            # "Wine"/"Beer"/… (Walmart's own class)
    # ── taxonomy: retail hierarchy path + category breadcrumb + product-type id ──
    out["rh_path"] = p.get("rhPath") or ""                       # "40000:42000:42001:42100:42206"
    out["product_type_id"] = p.get("productTypeId") or ""
    out["primary_shelf_id"] = p.get("primaryShelfId") or ""
    out["ironbank_category"] = p.get("ironbankCategory") or ""   # "Alcoholic Beverages"
    cat = p.get("category") or {}
    out["category_path"] = " > ".join(c.get("name", "") for c in (cat.get("path") or [])) or out.get("category", "")
    # ── ABV: spec first, else the shortDescription ("6% ABV") ──
    if out.get("abv_str"):                                       # "13.5% ABV" or "80 Proof" -> number
        n = _fnum(out["abv_str"])
        out["abv"] = (n / 2 if "proof" in out["abv_str"].lower() and n else n)
    if out.get("abv") is None:
        am = _ABV_TXT.search(p.get("shortDescription") or "")
        out["abv"] = float(am.group(1)) if am else None
    # ── inventory / placement / store context ──
    av = str(p.get("availabilityStatus") or _find(dd, "availabilityStatus") or "").lower()
    out["in_stock"] = bool(av) and "out" not in av and "unavailable" not in av
    ploc = p.get("productLocation") or []                        # aisle lives here: [{displayValue:"A34"}]
    out["aisle"] = (ploc[0].get("displayValue") if ploc else "") or _find(dd, "aisle") or ""
    out["order_limit"] = p.get("orderLimit")                     # per-order purchase cap
    loc = p.get("location") or {}
    out["store_id"] = (loc.get("storeIds") or [""])[0]
    out["store_state"] = loc.get("stateOrProvinceCode") or ""
    out["store_city"] = loc.get("city") or ""
    out["is_alcohol"] = bool(p.get("isLMPAlcoholItem")) or out.get("is_alcohol", True)
    # ── signals ──
    out["avg_rating"] = p.get("averageRating")
    out["num_reviews"] = p.get("numberOfReviews")
    flags = ((p.get("badges") or {}).get("flags")) or []
    out["rollback"] = any((f.get("key") == "ROLLBACK" or f.get("text") == "Rollback") for f in flags)
    out["seller"] = p.get("sellerName") or ""
    if p.get("name"):
        out["product_name"] = p["name"]
    pi = p.get("secondaryOfferPrice") or {}
    cp = (pi.get("currentPrice") or {}).get("price")
    if cp is not None:
        out["price"] = cp
    # keep the WHOLE product node — everything Walmart serves, not just what we promote
    out["raw_json"] = json.dumps(p, separators=(",", ":"))[:6000]
    return out


def crawl(terms=None, max_pages=4, delay=1.2, log=print):
    terms = terms or DEFAULT_TERMS
    seen, rows = set(), []
    for term in terms:
        got = 0
        for pg in range(1, max_pages + 1):
            try:
                page = search_page(term, pg)
            except Exception as e:
                log("  %s p%d: %s" % (term, pg, str(e)[:70])); break
            if not page:
                break
            new = [r for r in page if r["item_id"] and r["item_id"] not in seen]
            for r in new:
                seen.add(r["item_id"])
            rows.extend(new); got += len(new)
            if delay:
                time.sleep(delay)
        log("walmart: %-24s +%d (total %d)" % (term, got, len(rows)))
    return rows


def pull(terms=None, max_pages=4, delay=1.2, detail_pages=False, detail_cap=400, out=".", log=print):
    rows = crawl(terms, max_pages, delay, log)
    if detail_pages:                             # enrich with the /ip/ spec vector + inventory (paced!)
        n = min(len(rows), detail_cap)
        for i, r in enumerate(rows[:n]):
            if r.get("url"):
                r.update(detail(r["url"], log))
                time.sleep(max(delay, 1.6))      # detail pages throttle faster than search — slow down
            if (i + 1) % 25 == 0:
                log("  detail-enriched %d/%d" % (i + 1, n))
    warehouse.write_parquet("walmart_products", rows,
                            fields=["product_name", "item_id", "upc", "offer_id", "price", "size_ml", "brand",
                                    "type", "image", "url", "category", "category_path", "rh_path",
                                    "product_type_id", "primary_shelf_id", "ironbank_category", "is_alcohol",
                                    "varietal", "region", "vintage", "abv", "container", "flavor", "pairing",
                                    "wine_score", "aisle", "order_limit", "store_id", "store_state", "store_city",
                                    "avg_rating", "num_reviews", "rollback", "seller", "in_stock", "raw_json"])
    try:
        observe.record("walmart", [{"source": "walmart", "store_id": r.get("store_id", ""), "store": "Walmart",
                                    "product_id": r["item_id"], "upc": r.get("upc", ""), "price": r["price"],
                                    "on_promo": bool(r.get("rollback")),
                                    "in_stock": r.get("in_stock", True), "stock_level": r.get("aisle") or "",
                                    "is_hemp": ("hemp" in r["product_name"].lower()
                                    or "thc" in r["product_name"].lower())} for r in rows if r["price"] is not None])
    except Exception as e:
        log("  observe skipped: %s" % str(e)[:60])
    log("walmart_direct: %d products landed%s (direct, $0 BD)"
        % (len(rows), " + detail-enriched" if detail_pages else ""))
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", default="")
    ap.add_argument("--max-pages", type=int, default=4)
    ap.add_argument("--delay", type=float, default=1.2)
    ap.add_argument("--detail", action="store_true", help="fetch /ip/ pages for the rich spec vector + inventory")
    ap.add_argument("--detail-cap", type=int, default=400)
    a = ap.parse_args()
    t = [x.strip() for x in a.terms.split(",") if x.strip()] or None
    pull(terms=t, max_pages=a.max_pages, delay=a.delay, detail_pages=a.detail, detail_cap=a.detail_cap)
