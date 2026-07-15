#!/usr/bin/env python3
"""haskells.py — Haskell's Wine & Spirits (haskells.com, Minneapolis MN) full catalog + REAL inventory counts.

Haskell's runs on **BigCommerce** (same platform as ABC FWS), so it reuses that recipe: the product sitemap
is the whole catalog, and the BigCommerce **Storefront GraphQL API** — authorized by the public JWT embedded
in every product page — returns `inventory.aggregated.availableToSell`, the true on-hand unit count (not just
in/out). It's a single store (unlike ABC's per-store options), so one quantity per product.

We grab the WHOLE catalog (bev-alc first) and FLAG hemp/THC (Haskell's has a live `/thc/` category), so this
one source feeds both the bev-alc master and the hemp inventory view. This is a data vendor's Haskell's feed
done ourselves, with the count they can't reliably get (their extraction was pinned at 100 by pagination) —
the sitemap sidesteps pagination entirely.

Lands `haskells_products` (latest snapshot + raw) + the dated observe.record time-series (qty = availableToSell).
Polite (rate-limit/backoff/breaker via polite.py), stdlib + the shared warehouse/observe/hemp_scan layer.

    python haskells.py --limit 40      # smoke test (first 40 products)
    python haskells.py                 # full catalog -> haskells_products
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe
import hemp_scan
import polite

BASE = "https://haskells.com"
SITEMAP = BASE + "/xmlsitemap.php?type=products&page={}"
UA = os.environ.get("HASKELLS_UA", "HoodieSuite-Research/1.0 (+market-intelligence; respects robots)")
MIN_INT = float(os.environ.get("HASKELLS_MIN_INTERVAL", "0.6"))
STORE = "Haskell's - Minneapolis MN"

LOC_RE = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.I)
NAME_RE = re.compile(r'productView-title"[^>]*>\s*([^<]+?)\s*<', re.I)
UPC_RE = re.compile(r"UPC[^0-9]{0,12}(\d{8,14})", re.I)
TOKEN_RE = re.compile(r"eyJ0eXAiOiJKV1Qi[A-Za-z0-9_.-]{40,}")     # BigCommerce storefront JWT (public)
PROMO_RE = re.compile(r'(sale|save|\bwas\b|\d+%\s*off|reduced)', re.I)

# single-store product query: name, brand, sku, gtin(UPC), price, sale price, and per-variant on-hand
GQL = ('{ site { route(path: "%s") { node { ... on Product { entityId name sku '
       'brand { name } defaultImage { url(width: 400) } '
       'prices { price { value } retailPrice { value } salePrice { value } } '
       'variants(first: 50) { edges { node { sku gtin upc '
       'inventory { isInStock aggregated { availableToSell } } } } } } } } } }')

PROD_FIELDS = ["store", "product_id", "sku", "upc", "name", "brand", "category", "price", "retail_price",
               "on_sale", "in_stock", "qty", "url", "is_hemp", "hemp_signal", "image", "captured_at",
               "source", "raw_json"]


def fetch(url, timeout=30):
    body, h = polite.get(url, min_interval=MIN_INT, jitter=MIN_INT, timeout=timeout,
                         headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                  "User-Agent": UA, "Accept-Encoding": "identity"},
                         breaker_after=4, host="haskells.com", return_headers=True)
    return body.decode("utf-8", "replace")


def harvest_ids(max_pages=30, log=print):
    """Walk the product sitemaps → [(sku, url)] (sku = trailing path slug). type=products is the WHOLE catalog."""
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        try:
            body = fetch(SITEMAP.format(page))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                break
            log("sitemap page %d: HTTP %s" % (page, e.code)); break
        locs = [l for l in LOC_RE.findall(body) if "/" in l]
        prod = [(l.rstrip("/").rsplit("/", 1)[-1], l) for l in locs]
        prod = [(s, l) for s, l in prod if s and not l.endswith("/xmlsitemap.php")]
        if not prod:
            break
        for slug, loc in prod:
            if slug not in seen:
                seen.add(slug); out.append((slug, loc))
        log("sitemap page %d: %d products (total %d)" % (page, len(prod), len(out)))
    return out


def graphql_product(path, token):
    """One product via the storefront GraphQL. Returns a dict with a real on-hand qty, or None."""
    try:
        body = polite.get(BASE + "/graphql", data=json.dumps({"query": GQL % path}).encode(),
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                   "User-Agent": UA},
                          min_interval=MIN_INT, jitter=MIN_INT, timeout=25, breaker_after=4, host="haskells.com")
        r = json.loads(body.decode("utf-8", "replace"))
        node = (((r.get("data") or {}).get("site") or {}).get("route") or {}).get("node") or {}
        if not node:
            return None
        pr = node.get("prices") or {}
        price = ((pr.get("salePrice") or pr.get("price")) or {}).get("value")
        retail = ((pr.get("retailPrice") or pr.get("price")) or {}).get("value")
        on_sale = bool(pr.get("salePrice") and pr.get("salePrice", {}).get("value") is not None
                       and pr.get("price") and pr["salePrice"]["value"] < pr["price"]["value"])
        variants = (node.get("variants") or {}).get("edges") or []
        qty, in_stock, upc = 0, False, ""
        for e in variants:
            v = e["node"]; inv = v.get("inventory") or {}; agg = inv.get("aggregated") or {}
            a = agg.get("availableToSell")
            if a is not None:
                qty += a
            in_stock = in_stock or bool(inv.get("isInStock"))
            upc = upc or v.get("gtin") or v.get("upc") or ""
        return {"product_id": str(node.get("entityId") or ""), "sku": node.get("sku") or "",
                "name": node.get("name") or "", "brand": (node.get("brand") or {}).get("name") or "",
                "upc": upc, "price": price, "retail_price": retail, "on_sale": on_sale,
                "in_stock": in_stock, "qty": (qty if variants else None),
                "image": (node.get("defaultImage") or {}).get("url") or "", "raw": node}
    except Exception:
        return None


def _path(url):
    return "/" + url.split("//", 1)[-1].split("/", 1)[-1]


def fetch_product(slug, url, token_cache, log=print):
    """Product record via GraphQL (real qty), with an HTML fallback for name/upc/category."""
    body = fetch(url)
    if not token_cache.get("t"):
        m = TOKEN_RE.search(body)
        if m:
            token_cache["t"] = m.group(0)
    g = graphql_product(_path(url), token_cache["t"]) if token_cache.get("t") else None
    if not g:                                             # fallback: parse the page
        nm = NAME_RE.search(body); up = UPC_RE.search(body)
        g = {"product_id": "", "sku": slug, "name": nm.group(1).strip() if nm else slug,
             "brand": "", "upc": up.group(1) if up else "", "price": None, "retail_price": None,
             "on_sale": bool(PROMO_RE.search(body)), "in_stock": "InStock" in body, "qty": None,
             "image": "", "raw": {"fallback": True}}
    # category from breadcrumb / url; hemp flag
    cat = ""
    mcat = re.search(r'"category"\s*:\s*\[?"([^"\]]{2,40})"', body) or re.search(r'breadcrumb[^>]*>\s*<[^>]*>([^<]{2,40})', body)
    if mcat:
        cat = mcat.group(1).strip()
    sig = hemp_scan.classify(g["name"], cat) or (hemp_scan.classify(g["name"], "") if observe.is_hemp(g["name"]) else None)
    g["category"] = cat
    g["is_hemp"] = bool(sig) or observe.is_hemp(g["name"], cat)
    g["hemp_signal"] = sig or ""
    g["url"] = url
    return g


def run(limit=None, land=True, log=print):
    catalog = harvest_ids(log=log)
    if not catalog:
        log("[haskells] no products in sitemap — site structure may have changed."); return []
    if limit:
        catalog = catalog[:limit]
    log("[haskells] %d products to fetch" % len(catalog))
    ts = int(time.time())
    token_cache, snap, obs = {}, [], []
    for i, (slug, url) in enumerate(catalog):
        g = fetch_product(slug, url, token_cache, log=log)
        snap.append({"store": STORE, "product_id": g["product_id"], "sku": g["sku"], "upc": g["upc"],
                     "name": g["name"], "brand": g["brand"], "category": g.get("category", ""),
                     "price": g["price"], "retail_price": g["retail_price"], "on_sale": g["on_sale"],
                     "in_stock": g["in_stock"], "qty": g["qty"], "url": url, "is_hemp": g["is_hemp"],
                     "hemp_signal": g["hemp_signal"], "image": g["image"], "captured_at": ts,
                     "source": "haskells", "raw_json": json.dumps(g["raw"], separators=(",", ":"))[:4000]})
        obs.append({"store": STORE, "store_id": "haskells-mpls", "product_id": g["product_id"], "upc": g["upc"],
                    "brand": g["brand"], "name": g["name"], "price": g["price"], "on_promo": g["on_sale"],
                    "in_stock": g["in_stock"], "qty": g["qty"], "stock_level": "", "is_hemp": g["is_hemp"]})
        if (i + 1) % 100 == 0:
            log("  %d/%d …" % (i + 1, len(catalog)))
    if land and snap:
        warehouse.write_accumulate("haskells_products", snap,
                                   key=lambda r: r["sku"] or r["url"], fields=PROD_FIELDS)
        observe.record("haskells", obs, log=log)
    counted = sum(1 for r in snap if r["qty"] is not None)
    hemp = sum(1 for r in snap if r["is_hemp"])
    log("[haskells] DONE: %d products -> haskells_products (%d with an exact count, %d hemp)"
        % (len(snap), counted, hemp))
    return snap


def main(argv=None):
    ap = argparse.ArgumentParser(description="Haskell's (BigCommerce) full catalog + real inventory counts.")
    ap.add_argument("--limit", type=int, default=None, help="cap products fetched (smoke test)")
    ap.add_argument("--no-land", action="store_true")
    a = ap.parse_args(argv)
    recs = run(limit=a.limit, land=not a.no_land)
    for r in recs[:25]:
        print("  %-40s $%-7s qty=%-5s %s" % ((r["name"] or "")[:40], r["price"], r["qty"],
                                             "HEMP" if r["is_hemp"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
