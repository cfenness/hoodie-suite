"""abc_catalog.py — the ABC FWS PRODUCT CATALOG (sku -> name / brand / size / upc / price).

The ABC availability tracker (abc_fws_scraper) reads per-store in/out but never captures the product NAME, so
abc_products has 1.87M rows with blank name/brand — useless for master corroboration. This pass reuses the
scraper's sitemap harvest + fetch, pulls og:title / brand / gtin / price off each BigCommerce product page
(they render fine here), and lands abc_catalog — the single biggest independent corroboration source for the
master (master_ttb), on ~13.9k real products with UPCs.

    python abc_catalog.py --limit 200     # bounded proof
    python abc_catalog.py                  # full catalog (concurrent, ~20-40 min)
"""
import argparse, html as _html, os, re, sys, threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import abc_fws_scraper as abc

_NAME = re.compile(r'property="og:title"\s+content="([^"]+)"|"og:title"[^>]*content="([^"]+)"', re.I)
_BRAND = re.compile(r'"brand":\s*\{[^}]*"name":\s*"([^"]+)"|itemprop="brand"[^>]*content="([^"]+)"', re.I)
_SIZE = re.compile(r'(\d+(?:\.\d+)?)\s?(ml|l|liter|litre|oz)\b', re.I)
_UPC = re.compile(r'"gtin1?[234]?":\s*"?(\d{8,14})"?|itemprop="gtin\d*"[^>]*content="(\d{8,14})"', re.I)
_PRICE = re.compile(r'(?:og:price:amount|product:price:amount)"[^>]*content="([\d.]+)"', re.I)
_IMG = re.compile(r'(?:og:image|twitter:image)"[^>]*content="([^"]+)"', re.I)
_DESC = re.compile(r'(?:og:description|"description")"[^>]*content="([^"]+)"', re.I)


def _f(m):
    return next((g for g in m.groups() if g), None) if m else None


def one(sku, url):
    try:
        body, _ = abc.fetch(url)
    except Exception:
        return None
    nm = _f(_NAME.search(body))
    if not nm:
        return None
    name = _html.unescape(nm).split(" | ")[0].strip()
    pm = _PRICE.search(body)
    im = _IMG.search(body); dm = _DESC.search(body)
    return {"sku": sku, "name": name, "brand": _html.unescape(_f(_BRAND.search(body)) or "").strip(),
            "size": (_SIZE.search(name).group(0) if _SIZE.search(name) else ""),
            "upc": _f(_UPC.search(body)) or "", "price": (float(pm.group(1)) if pm else None),
            "image": (im.group(1) if im else ""),
            "description": (_html.unescape(dm.group(1))[:2000] if dm else ""), "url": url}


def run(limit=None, workers=12, log=print):
    cat = abc.harvest_ids(log=log)                              # [(sku, url)] from the product sitemaps
    if limit:
        cat = cat[:limit]
    log("[abc-cat] crawling %d products (%d workers)…" % (len(cat), workers))
    rows, lock = [], threading.Lock()

    def w(t):
        r = one(*t)
        if r:
            with lock:
                rows.append(r)
                if len(rows) % 500 == 0:
                    warehouse.write_accumulate("abc_catalog", rows,   # checkpoint (accumulates, incl. a --limit run)
                                               key=lambda r: r.get("sku") or r.get("upc") or r.get("url"))
                    log("  ...%d named" % len(rows))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(w, cat))
    if rows:
        warehouse.write_accumulate("abc_catalog", rows, key=lambda r: r.get("sku") or r.get("upc") or r.get("url"))
    got = sum(1 for r in rows if r.get("upc"))
    log("[abc-cat] DONE %d products (%d with UPC) -> abc_catalog" % (len(rows), got))
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    run(limit=a.limit or None, workers=a.workers)
