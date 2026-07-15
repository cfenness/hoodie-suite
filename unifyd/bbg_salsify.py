#!/usr/bin/env python3
"""bbg_salsify.py — Breakthru Beverage Group public product catalog (a Salsify Next.js SSG microsite).

BBG is a top-3 US beer/wine/spirits WHOLESALER. Its public "Master" catalog exposes 55,774 products as clean
JSON with SUPPLIER attribution + rich facets — the supplier tier AND a huge structured product set in one place:
  supplier · family · product category · dimensions/size · region · varietal · flavor · US market region · ABV ·
  bottles-per-case · image (with the UPC/GTIN in the filename).

Salsify catalog sites are a REPEATABLE RECIPE — the data is at `/<site>/_next/data/<buildId>/…`:
  • list page N  -> products/<N>.json?params=products&params=<N>   (16 products/page)
  • product      -> product/<id>/<slug>.json?id=<id>&title=<slug>  (full attributes)
  • facets       -> index.json .pageProps.productFacets              (the dictionaries + supplier list)
The buildId changes on redeploy, so we read it live from the site's __NEXT_DATA__. Same shape works for any
other distributor's Salsify public catalog (change SITE). Polite, stdlib, resumable (accumulates, skips done).

    python bbg_salsify.py --pages 5      # smoke test (5 list pages)
    python bbg_salsify.py                 # full catalog -> bbg_products
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

SITE = ("https://sites.salsify.com/1cb19d26-0728-4fb1-afe0-3b8c309b6180/"
        "d28396be-a4b8-4891-9533-c40780422895")
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0 Safari/537.36")}
FLD = ["id", "title", "brand", "supplier", "family", "category", "dimensions", "size_ml", "region", "varietal",
       "flavor", "market_region", "abv", "bottles_per_case", "image", "upc", "wholesaler", "source", "raw_json"]
_GTIN = re.compile(r"(\d{12,14})")
_SIZE = re.compile(r"([\d.]+)\s*(ML|L|LT|OZ)", re.I)


def _get(url, timeout=30, raw=False):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()
    return r if raw else json.loads(r)


def build_id():
    """Live buildId from the site's __NEXT_DATA__ — survives Salsify redeploys."""
    h = _get(SITE + "/", raw=True).decode("utf-8", "replace")
    m = re.search(r'"buildId":"([^"]+)"', h)
    if not m:
        raise RuntimeError("could not read buildId")
    return m.group(1)


def _data(bid, path):
    return _get("%s/_next/data/%s/%s" % (SITE, bid, path))


def facets(bid=None):
    """The catalog facets (supplier list + varietal/region/flavor/category/family dictionaries)."""
    bid = bid or build_id()
    return _data(bid, "index.json")["pageProps"].get("productFacets", {})


def _to_ml(dim):
    m = _SIZE.search(dim or "")
    if not m:
        return None
    v = float(m.group(1)); u = m.group(2).lower()
    return round(v * (1000 if u.startswith("l") and u != "oz" else (29.57 if u == "oz" else 1)))


def parse_product(pr):
    """Flatten a product-detail JSON into one record (facet filterProperties + the Product-Details property set)."""
    fp = {}
    for f in (pr.get("filterProperties") or []):
        if isinstance(f, dict) and f.get("property"):
            vals = f.get("values") or []
            fp[f["property"]] = vals[0] if vals else ""
    props = {}
    for s in (pr.get("propertySets") or []):
        for p in (s.get("properties") or []):
            vals = p.get("values") or []
            props[p.get("property")] = vals[0] if vals else ""
    img = ""
    imgs = pr.get("images") or []
    if imgs:
        img = imgs[0].get("value") or ""
        upc_src = imgs[0].get("name") or ""
    else:
        upc_src = ""
    gm = _GTIN.search(upc_src)
    dims = fp.get("Dimensions") or ""
    return {"id": str(pr.get("id") or ""), "title": (pr.get("title") or "").strip(),
            "brand": (pr.get("subtitle") or pr.get("listSubtitle") or "").strip(),
            "supplier": fp.get("Supplier") or props.get("Supplier") or "",
            "family": fp.get("Family") or "", "category": fp.get("Product Category") or "",
            "dimensions": dims, "size_ml": _to_ml(dims),
            "region": fp.get("SAP Region Description") or "", "varietal": fp.get("SAP Varietal Grape Type Description") or "",
            "flavor": fp.get("SAP Flavor Profile") or "", "market_region": fp.get("US Market Region") or "",
            "abv": props.get("Spirit Proof") or props.get("ABV") or "",
            "bottles_per_case": props.get("Bottles Per Case") or "",
            "image": img, "upc": (gm.group(1) if gm else ""), "wholesaler": "BBG", "source": "bbg",
            "raw_json": json.dumps({"filterProperties": fp, "properties": props}, separators=(",", ":"))[:6000]}


def list_page(bid, n):
    """(id, slug) for the 16 products on list page n."""
    try:
        pp = _data(bid, "products/%d.json?params=products&params=%d" % (n, n))["pageProps"]
    except Exception:
        return []
    out = []
    for g in (pp.get("productGroups") or []):
        for p in (g.get("products") or []):
            pid = p.get("id"); slug = p.get("slugifiedTitle") or p.get("slugifiedId")
            if pid:
                out.append((str(pid), slug or str(pid)))
    return out


def detail(bid, pid, slug):
    d = _data(bid, "product/%s/%s.json?id=%s&title=%s" % (pid, slug, pid, slug))
    return parse_product(d["pageProps"]["product"])


def pull(pages=None, workers=16, log=print):
    bid = build_id()
    total_pages = _data(bid, "index.json")["pageProps"].get("totalPages") or 1
    pages = min(pages or total_pages, total_pages)
    log("[bbg] buildId=%s · %d list pages (of %d)" % (bid, pages, total_pages))
    try:
        done = {r["id"] for r in warehouse.query("bbg_products", "SELECT id FROM t")}
    except Exception:
        done = set()
    # enumerate ids across list pages (threaded)
    ids = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(lambda n: list_page(bid, n), range(1, pages + 1)):
            ids.extend(res)
    todo = [(pid, slug) for pid, slug in ids if pid not in done]
    log("[bbg] %d products listed, %d already have detail, %d to fetch" % (len(ids), len(done), len(todo)))
    landed = 0
    for i in range(0, len(todo), 400):                    # land in chunks -> resumable
        chunk = todo[i:i + 400]
        recs = []
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(lambda t: _safe_detail(bid, t), chunk):
                if r:
                    recs.append(r)
        if recs:
            warehouse.write_accumulate("bbg_products", recs, key=lambda r: r["id"], fields=FLD)
            landed += len(recs)
            log("[bbg] +%d (%d/%d)" % (len(recs), min(i + 400, len(todo)), len(todo)))
    log("[bbg] DONE: %d product details landed -> bbg_products" % landed)
    return landed


def _safe_detail(bid, t):
    try:
        return detail(bid, t[0], t[1])
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="BBG (Breakthru Beverage) Salsify catalog scraper.")
    ap.add_argument("--pages", type=int, default=None, help="limit list pages (default: all)")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args(argv)
    pull(pages=a.pages, workers=a.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
