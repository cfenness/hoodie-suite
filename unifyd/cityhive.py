#!/usr/bin/env python3
"""cityhive.py — FULL-CAPTURE City Hive scraper, NO Bright Data.

City Hive is the biggest independent-liquor e-commerce platform (~2000 retailers). Its stores are Cloudflare-gated,
so the SEO-surface recipe (off_premise.cityhive_catalog) only works through Bright Data. This module does it BD-free
with our OWN Chrome via browser_warm + patchright: patchright clears the Cloudflare challenge once, then IN-PAGE
FETCH reuses that trusted session to pull the server-rendered pages. Per store: fetch the product sitemap → every
product URL → each product page yields the full record from its JSON-LD Product + OpenGraph (name/brand/price/size/
upc/description/image), via off_premise's proven parsers. Lands to cityhive_products (accumulate, key store|url).
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser_warm
import off_premise as op


def _domain(base):
    return re.sub(r"^https?://(www\.)?", "", base).split("/")[0]


class Store:
    """A warmed City Hive store: Cloudflare cleared once (patchright), in-page fetch ready."""

    def __init__(self, base, log=print):
        self.base = base.rstrip("/")
        self.log = log
        self._w = None

    def __enter__(self):
        self._w = browser_warm.Warmer(_domain(self.base), channel="chrome", headful=True, patchright=True).__enter__()
        p = self._w._page()
        p.goto(self.base + "/", wait_until="domcontentloaded", timeout=90000)
        for _ in range(20):                                  # ride out the Cloudflare challenge
            p.wait_for_timeout(2500)
            try:
                self._w.human(1)
            except Exception:
                pass
            body = p.evaluate("() => document.body ? document.body.innerText.slice(0,120) : ''") or ""
            if not re.search(r"just a moment|checking your browser|cloudflare|verify you are human", body, re.I):
                break
        return self

    def __exit__(self, *a):
        try:
            self._w.__exit__(*a)
        except Exception:
            pass

    def fetch(self, url):
        try:
            st, body = self._w.fetch(url)
            return body if st == 200 else ""
        except Exception:
            return ""

    def settle(self, ms=100):
        try:
            self._w._page().wait_for_timeout(ms)
        except Exception:
            pass


def _sitemap_products(store):
    """Every product URL via the store's sitemap (in-page fetch past Cloudflare). Reuses the candidate logic from
    off_premise._ch_sitemap_products (/sitemap.xml, /googlesitemapxml.xml, robots Sitemap: lines)."""
    base = store.base
    cands = [base + "/sitemap.xml", base + "/googlesitemapxml.xml"]
    try:
        robots = store.fetch(base + "/robots.txt")
        for sm in re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", robots or ""):
            if sm not in cands:
                cands.append(sm.strip())
    except Exception:
        pass
    for url in cands:
        xml = store.fetch(url)
        urls = [l for l in re.findall(r"<loc>([^<]+)</loc>", xml or "") if "/shop/product/" in l]
        # a sitemap index → follow child product sitemaps
        if not urls and xml and "<sitemapindex" in xml:
            for child in re.findall(r"<loc>([^<]+)</loc>", xml):
                cx = store.fetch(child)
                urls += [l for l in re.findall(r"<loc>([^<]+)</loc>", cx or "") if "/shop/product/" in l]
                store.settle(80)
        if urls:
            return list(dict.fromkeys(urls))
    return []


def pull_store(base, max_products=None, log=print):
    """Full catalog for one City Hive store: sitemap → each product page (in-page fetch) → off_premise's parser."""
    recs = []
    with Store(base, log=log) as store:
        urls = _sitemap_products(store)
        log("[cityhive] %s: %d products in sitemap" % (base, len(urls)))
        if max_products:
            urls = urls[:max_products]
        for i, u in enumerate(urls):
            html = store.fetch(u)
            if not html:
                continue
            try:
                d = op._ch_parse_product(html)
            except Exception:
                d = {}
            if d.get("name"):
                oid = (re.search(r"option-id=([0-9a-f]+)", u) or [None, ""])[1]
                recs.append(dict(name=d["name"], brand=d.get("brand") or "", price=d.get("price"),
                                 sku=d.get("sku") or d.get("pid") or "", upc=d.get("upc") or "",
                                 size_ml=d.get("size_ml"), category=d.get("category") or "",
                                 description=d.get("description") or "", store=d.get("store") or "",
                                 image=d.get("image") or "", url=u.split("?")[0], option_id=oid or "",
                                 raw_json=json.dumps(d, separators=(",", ":"))[:8000]))
            if (i + 1) % 100 == 0:
                log("  [cityhive] %d/%d parsed, %d products (%d priced, %d upc)" % (
                    i + 1, len(urls), len(recs), sum(1 for r in recs if r["price"]), sum(1 for r in recs if r["upc"])))
            store.settle(90)
    log("[cityhive] %s: %d products (%d priced, %d upc)" % (
        base, len(recs), sum(1 for r in recs if r["price"]), sum(1 for r in recs if r["upc"])))
    return recs


CH_FIELDS = ["store_base", "store", "name", "brand", "price", "sku", "upc", "size_ml", "category",
             "description", "image", "url", "option_id", "is_hemp", "captured_at", "raw_json"]


def land(recs, base, log=print):
    import warehouse
    import observe
    ts = int(time.time())
    for r in recs:
        r["store_base"] = base
        r["captured_at"] = ts
        r["is_hemp"] = observe.is_hemp(r.get("name", ""), r.get("category", ""))
    warehouse.write_accumulate("cityhive_products", recs,
                               key=lambda r: (r.get("store_base"), r.get("url") or r.get("sku") or r.get("name")),
                               fields=CH_FIELDS)
    observe.record("cityhive", [dict(source="cityhive", store_id=base, store=r.get("store") or base,
                                     product_id=r.get("sku") or (r.get("name") or "")[:90], upc=r.get("upc", ""),
                                     brand=r.get("brand", ""), name=r.get("name", ""), price=r.get("price"),
                                     on_promo=False, in_stock=True, qty=None, stock_level="",
                                     is_hemp=r.get("is_hemp", False))
                                for r in recs if r.get("price") is not None], log=log)
    log("[cityhive] landed %d products → cityhive_products + retail_observations" % len(recs))


def national(max_stores=None, max_products=None, log=print):
    """Discover City Hive stores (fingerprint) + the named chains, full-capture each in its own subprocess."""
    import subprocess
    import glob
    stores = list(dict.fromkeys(
        [c[1] for c in op.CITYHIVE_CHAINS] + op.discover_stores('"powered by cityhive" OR "cityhive" liquor wine spirits', pages=6, log=log)))
    stores = [s for s in stores if not re.search(r"cityhive\.com|facebook|trustpilot|apps\.apple", s, re.I)]
    if max_stores:
        stores = stores[:max_stores]
    here = os.path.dirname(os.path.abspath(__file__))
    total, ok = 0, 0
    for i, base in enumerate(stores):
        base = base if base.startswith("http") else "https://" + base
        log("[cityhive] store %d/%d: %s" % (i + 1, len(stores), base))
        for lk in glob.glob(os.path.expanduser("~/.hoodie_browser_profiles/*/Singleton*")):
            try:
                os.remove(lk)
            except Exception:
                pass
        args = ["--store", base] + (["--max-products", str(max_products)] if max_products else [])
        code = ("import sys; sys.path.insert(0, %r); import kroger_api; kroger_api._load_creds(); "
                "import cityhive; cityhive.main(%r)" % (here, args))
        try:
            r = subprocess.run([sys.executable, "-c", code], cwd=here, timeout=10800, capture_output=True, text=True)
            m = re.search(r'"products":\s*(\d+)', r.stdout or "")
            n = int(m.group(1)) if m else 0
            if n:
                total += n
                ok += 1
            log("  → %d products" % n)
        except Exception as e:
            log("  %s ERR %s" % (base, str(e)[:90]))
    log("[cityhive] NATIONAL done: %d products across %d/%d stores" % (total, ok, len(stores)))
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description="Full-capture City Hive scraper (no Bright Data).")
    ap.add_argument("--store", help="a City Hive store base URL")
    ap.add_argument("--national", action="store_true")
    ap.add_argument("--max-stores", type=int, default=0)
    ap.add_argument("--max-products", type=int, default=0)
    ap.add_argument("--no-land", action="store_true")
    a = ap.parse_args(argv)
    if a.national:
        print(json.dumps({"mode": "national", "products": national(max_stores=(a.max_stores or None),
                                                                   max_products=(a.max_products or None))}))
        return 0
    if not a.store:
        print("need --store <url> (or --national)"); return 2
    recs = pull_store(a.store, max_products=(a.max_products or None))
    if recs and not a.no_land:
        try:
            land(recs, a.store)
        except Exception as e:
            print("land skipped:", str(e)[:100])
    print(json.dumps({"store": a.store, "products": len(recs),
                      "priced": sum(1 for r in recs if r.get("price")),
                      "with_upc": sum(1 for r in recs if r.get("upc")), "sample": recs[:2]}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
