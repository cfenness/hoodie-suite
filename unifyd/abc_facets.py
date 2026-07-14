"""abc_facets.py — harvest ABC's OWN taxonomy via its SearchSpring API (BigCommerce + SearchSpring, siteId p16j4k).

The insight: a chain's faceted navigation IS a maintained controlled taxonomy. Instead of parsing each PDP or
guessing attributes from the product name, we read the retailer's faceted data. Every product carries
`categories_hierarchy` — its DRILL-PATH memberships (Wine>Shop By Varietal>Cabernet Sauvignon, Wine>Shop By
Type>Red Wine, region, country) — plus custom_class / size / brand / price / rating / per-store stock. We parse
the drill paths into category/type/varietal/region: AUTHORITATIVE (the retailer tagged it), self-updating
(re-crawl catches new facets), and it feeds the dictionaries + the declarative matcher without hand-mining.

This is the SearchSpring RECIPE — the same shape works for any SearchSpring retailer (just the siteId changes).
Lands `abc_products` (rich catalog) + accumulates `source_taxonomy` (the facet vocabularies per source/axis).

    python abc_facets.py            # full harvest (~14k products)
"""
import argparse
import gzip
import html as _html
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

SITE = "p16j4k"
# SearchSpring caps deep paging at 10k results, so harvest per TOP CATEGORY (each < 10k) — this also completes
# the alcohol catalog cleanly and tags each product with its top category.
CATS = ["Wine", "Spirits", "Beer", "Ready To Drink"]
API = ("https://%s.a.searchspring.io/api/search/search.json?siteId=%s"
       "&resultsFormat=native&resultsPerPage=%d&page=%d&q=&bgfilter.categories_hierarchy=%s")
FLD = ["sku", "uid", "name", "brand", "url", "category", "type", "varietal", "region", "country", "class",
       "size", "price", "msrp", "rating", "rating_count", "in_stock", "on_sale", "source_certified", "image", "raw_json"]


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                               "AppleWebKit/605.1.15 Safari/605.1.15", "Accept-Encoding": "gzip"})
    raw = urllib.request.urlopen(req, timeout=45).read()
    return gzip.decompress(raw).decode("utf-8", "replace") if raw[:2] == b"\x1f\x8b" else raw.decode("utf-8", "replace")


def _paths(ch):
    """Parse categories_hierarchy -> {category, type, varietal, region, country} from the drill paths."""
    if isinstance(ch, str):
        try:
            ch = json.loads(ch.replace("'", '"'))
        except Exception:
            ch = [ch]
    out = {}
    for p in (ch or []):
        segs = [s.strip() for s in _html.unescape(str(p)).split(">")]
        if segs and "category" not in out:
            out["category"] = segs[0]
        if len(segs) >= 3:
            axis, val = segs[-2].lower(), segs[-1]
            if val.lower().startswith("shop all") or val.lower() == "featured":
                continue                             # catch-all leaves, not a real facet value
            if "varietal" in axis:
                out["varietal"] = val
            elif "type" in axis:
                out["type"] = val
            elif "sub" in axis and "region" in axis:
                out.setdefault("sub_region", val)
            elif "region" in axis:
                out["region"] = val
            elif "country" in axis:
                out["country"] = val
    return out


def pull(cap=None, page_size=100, delay=0.15, log=print):
    import urllib.parse
    products, taxonomy, seen = [], {}, set()
    for cat in CATS:
        page, total = 1, None
        while True:
            try:
                d = json.loads(_get(API % (SITE, SITE, page_size, page, urllib.parse.quote(cat))))
            except Exception as e:
                log("[abc_facets] %s page %d: %s" % (cat, page, str(e)[:60])); break
            total = (d.get("pagination") or {}).get("totalResults") or total
            res = d.get("results") or []
            if not res:
                break
            for r in res:
                uid = r.get("uid") or r.get("sku")
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                pp = _paths(r.get("categories_hierarchy"))
                for ax in ("type", "varietal", "region", "country"):
                    if pp.get(ax):
                        taxonomy.setdefault(ax, set()).add(pp[ax])
                products.append({
                    "sku": r.get("sku", ""), "uid": str(uid), "name": _html.unescape(r.get("name", "")),
                    "brand": r.get("brand", ""), "url": "https://abcfws.com" + (r.get("url") or ""),
                    "category": pp.get("category", "") or cat, "type": pp.get("type", ""),
                    "varietal": pp.get("varietal", ""), "region": pp.get("region", ""),
                    "country": pp.get("country", ""), "class": r.get("custom_class", ""),
                    "size": r.get("custom_item_size", ""), "price": r.get("price", ""), "msrp": r.get("msrp", ""),
                    "rating": r.get("rating", ""), "rating_count": r.get("ratingCount", ""),
                    "in_stock": r.get("ss_in_stock", ""), "on_sale": r.get("ss_on_sale", ""),
                    "source_certified": r.get("custom_source_certified", ""), "image": r.get("imageUrl", ""),
                    "raw_json": json.dumps({k: v for k, v in r.items() if not str(k).startswith("intelli")},
                                           separators=(",", ":"))})
            log("[abc_facets] %-14s page %d  %d products (cat total %s)" % (cat, page, len(products), total))
            page += 1
            if len(res) < page_size or (cap and len(products) >= cap):
                break
            if delay:
                time.sleep(delay)
        if cap and len(products) >= cap:
            break
    warehouse.write_parquet("abc_products", products, fields=FLD)
    tax = [{"source": "abc", "axis": ax, "value": v} for ax, vs in taxonomy.items() for v in sorted(vs)]
    if tax:
        warehouse.write_accumulate("source_taxonomy", tax, key=lambda r: (r["source"], r["axis"], r["value"]))
    log("[abc_facets] DONE: %d products -> abc_products | taxonomy %s"
        % (len(products), {ax: len(vs) for ax, vs in taxonomy.items()}))
    return {"products": len(products), "taxonomy": {ax: len(vs) for ax, vs in taxonomy.items()}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=None)
    a = ap.parse_args()
    pull(cap=a.cap)
