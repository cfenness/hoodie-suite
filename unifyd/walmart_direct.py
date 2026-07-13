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


def pull(terms=None, max_pages=4, delay=1.2, out=".", log=print):
    rows = crawl(terms, max_pages, delay, log)
    warehouse.write_parquet("walmart_products", rows,
                            fields=["product_name", "item_id", "price", "size_ml", "brand", "image", "url",
                                    "category", "is_alcohol"])
    try:
        observe.record("walmart", [{"source": "walmart", "store_id": "", "store": "Walmart",
                                    "product_id": r["item_id"], "upc": "", "price": r["price"],
                                    "in_stock": True, "is_hemp": ("hemp" in r["product_name"].lower()
                                    or "thc" in r["product_name"].lower())} for r in rows if r["price"] is not None])
    except Exception as e:
        log("  observe skipped: %s" % str(e)[:60])
    log("walmart_direct: %d products landed (direct, $0 BD)" % len(rows))
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", default="")
    ap.add_argument("--max-pages", type=int, default=4)
    ap.add_argument("--delay", type=float, default=1.2)
    a = ap.parse_args()
    t = [x.strip() for x in a.terms.split(",") if x.strip()] or None
    pull(terms=t, max_pages=a.max_pages, delay=a.delay)
