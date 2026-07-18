#!/usr/bin/env python3
"""bottlecapps.py — FULL-CAPTURE Bottlecapps (liquorapps) scraper.

Bottlecapps powers hundreds of independent liquor retailers' e-commerce — DataDome-protected JS storefronts where
everything is keyed by store_id (category = /s-<sid>/c-N/buy-<slug>, product = /product/s-<sid>/p-<pid>/buy-<slug>).

NO Bright Data: our OWN Chrome via browser_warm + patchright clears DataDome once (the CDP-automation leak DataDome
scores on is patched), then IN-PAGE FETCH reuses that trusted session to pull the server-rendered HTML. Per store:
enumerate every category → union all product URLs → each product page yields FULL structured data from its JSON-LD
`Product` block + the `<input name="upc">` hidden field: name, brand, UPC, sku, price, size, availability,
description, rating, image. Everything is captured, incl. raw_json (the source fields), per the full-capture directive.

Supersedes off_premise.bottlecapps_catalog — which was a shallow sample (max 10 categories, 8 scrolls, ~60 products/
store, NO price, NO UPC, on Bright Data). Discovery: SERP the platform fingerprint → store domains (find_stores).
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser_warm

_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*-?\s*(ml|l|liter|ltr|oz|pk|pack|gal)\b", re.I)


def _domain(base):
    return re.sub(r"^https?://(www\.)?", "", base).split("/")[0]


# ── the parsers (proven against real douglaswine.com markup) ──────────────────────────────────────────────────
def parse_product(html, url=""):
    """Product page → full record from the JSON-LD Product block + the upc hidden input + a price fallback."""
    rec = {"url": url.split("?")[0]}
    ld = None
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            d = json.loads(block.strip())
        except Exception:
            continue
        for node in (d if isinstance(d, list) else [d]):
            if isinstance(node, dict) and node.get("@type") == "Product":
                ld = node
    if ld:
        rec["name"] = (ld.get("name") or "").strip()
        b = ld.get("brand")
        rec["brand"] = (b.get("name") if isinstance(b, dict) else b) or ""
        rec["sku"] = str(ld.get("sku") or "")
        rec["gtin"] = str(ld.get("gtin14") or ld.get("gtin13") or ld.get("gtin") or "")
        rec["description"] = (ld.get("description") or "")[:600]
        img = ld.get("image")
        rec["image"] = img if isinstance(img, str) else ((img or [""])[0] if isinstance(img, list) else "")
        off = ld.get("offers") or {}
        off = off[0] if isinstance(off, list) else off
        try:
            rec["price"] = float(off.get("price")) if off.get("price") not in (None, "") else None
        except Exception:
            rec["price"] = None
        rec["currency"] = off.get("priceCurrency") or "USD"
        rec["availability"] = (off.get("availability") or "").split("/")[-1]
        ar = ld.get("aggregateRating") or {}
        rec["rating"] = ar.get("ratingValue")
        rec["rating_count"] = ar.get("reviewCount") or ar.get("ratingCount")
    # the UPC lives in a hidden input (JSON-LD gtin14 is usually blank)
    um = re.search(r'name="upc"\s+value="(\d{6,14})"', html)
    rec["upc"] = um.group(1) if um else (rec.get("gtin") or "")
    if not rec.get("price"):
        pm = re.search(r'class="price_new"[^>]*>\s*\$?\s*([\d,]+\.\d{2})', html)
        rec["price"] = float(pm.group(1).replace(",", "")) if pm else None
    sz = _SIZE.search(url) or _SIZE.search(rec.get("name", ""))
    rec["size"] = sz.group(0) if sz else ""
    pm2 = re.search(r"/p-(\d+)/", url)
    rec["pid"] = pm2.group(1) if pm2 else ""
    return rec


# ── warmed store session ──────────────────────────────────────────────────────────────────────────────────────
class Store:
    """A warmed Bottlecapps store: DataDome cleared once, store_id + full category list known, in-page fetch ready."""

    def __init__(self, base, log=print):
        self.base = base.rstrip("/")
        self.log = log
        self.sid = None
        self.cats = []
        self._w = None

    def __enter__(self):
        self._w = browser_warm.Warmer(_domain(self.base), channel="chrome", headful=True, patchright=True).__enter__()
        p = self._w._page()

        def try_clear(tries):
            for _ in range(tries):                           # ride out DataDome; the app hydrates the store_id
                p.wait_for_timeout(2500)
                try:
                    self._w.human(1)
                except Exception:
                    pass
                html = p.content()
                m = re.search(r'store_id\s*=\s*"?(\d{3,7})', html) or re.search(r"/s-(\d{3,7})/", html)
                if m:
                    return m.group(1)
            return None
        p.goto(self.base + "/", wait_until="domcontentloaded", timeout=90000)
        self.sid = try_clear(20)
        if not self.sid:                                     # DataDome sometimes needs a reload to serve the app
            try:
                p.goto(self.base + "/", wait_until="domcontentloaded", timeout=90000)
            except Exception:
                pass
            self.sid = try_clear(16)
        if not self.sid:
            raise RuntimeError("store_id not found — DataDome not cleared for %s" % self.base)
        self.cats = p.eval_on_selector_all(
            "a", "els=>Array.from(new Set(els.map(a=>a.href).filter(h=>/\\/s-\\d+\\/c-\\d+\\/buy-/.test(h))))")
        self.log("[bottlecapps] %s  store_id=%s  %d categories" % (self.base, self.sid, len(self.cats)))
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

    def settle(self, ms=120):
        try:
            self._w._page().wait_for_timeout(ms)
        except Exception:
            pass


def discover_products(store):
    """Union of every product URL across the store's categories (in-page fetch each category's server-rendered HTML)."""
    urls = set()
    for i, c in enumerate(store.cats):
        html = store.fetch(c)
        for u in re.findall(r"/product/s-%s/p-\d+/buy-[a-z0-9\-]+" % store.sid, html or ""):
            urls.add(store.base + u)
        if (i + 1) % 25 == 0:
            store.log("  [discover] %d/%d categories → %d products" % (i + 1, len(store.cats), len(urls)))
        store.settle(120)
    store.log("[bottlecapps] %s: discovered %d distinct products" % (store.base, len(urls)))
    return sorted(urls)


def pull_store(base, max_products=None, detail=True, log=print):
    """Full catalog for one store: discover product URLs, then pull each product page for complete structured data."""
    recs = []
    with Store(base, log=log) as store:
        urls = discover_products(store)
        if max_products:
            urls = urls[:max_products]
        if not detail:
            return [{"url": u, "store": base, "store_id": store.sid,
                     "pid": (re.search(r"/p-(\d+)/", u) or [None, ""])[1]} for u in urls]
        for i, u in enumerate(urls):
            html = store.fetch(u)
            if not html:
                continue
            r = parse_product(html, u)
            if r.get("name"):
                r["store"] = base
                r["store_id"] = store.sid
                recs.append(r)
            if (i + 1) % 50 == 0:
                log("  [pull] %d/%d products (%d w/ upc, %d priced)" % (
                    i + 1, len(urls), sum(1 for x in recs if x.get("upc")), sum(1 for x in recs if x.get("price"))))
            store.settle(120)
    log("[bottlecapps] %s: %d products captured (%d w/ upc, %d priced)" % (
        base, len(recs), sum(1 for x in recs if x.get("upc")), sum(1 for x in recs if x.get("price"))))
    return recs


BC_FIELDS = ["store", "store_id", "pid", "url", "name", "brand", "upc", "sku", "gtin", "price", "currency",
             "size", "availability", "description", "image", "rating", "rating_count", "captured_at", "raw_json"]


def land(recs, log=print):
    """Persist: bottlecapps_products catalog (accumulate, key store|pid) + retail_observations time-series."""
    import warehouse
    import observe
    ts = int(time.time())
    for r in recs:
        r["captured_at"] = ts
        r["raw_json"] = json.dumps({k: r.get(k) for k in
                                    ("name", "brand", "upc", "sku", "gtin", "price", "size", "availability",
                                     "description", "rating", "rating_count", "image")}, separators=(",", ":"))
    warehouse.write_accumulate("bottlecapps_products", recs,
                               key=lambda r: (r.get("store"), r.get("pid")), fields=BC_FIELDS)
    observe.record("bottlecapps", [dict(source="bottlecapps", store_id=r.get("store_id", ""),
                                        store=r.get("store", ""), product_id=r.get("pid", ""),
                                        upc=r.get("upc", ""), brand=r.get("brand", ""), name=r.get("name", ""),
                                        price=r.get("price"), on_promo=False,
                                        in_stock=(r.get("availability") == "InStock"), qty=None,
                                        stock_level=r.get("availability", ""), is_hemp=False)
                                   for r in recs if r.get("price") is not None], log=log)
    log("[bottlecapps] landed %d products → bottlecapps_products + retail_observations" % len(recs))


# ── store discovery — SERP the Bottlecapps fingerprint → independent-retailer domains ─────────────────────────
_BC_SIG = '"powered by bottlecapps" OR "bottlecapps" liquor wine spirits order online'


_NOT_STORE = re.compile(r"grubhub|trustpilot|newswire|doordash|instacart|facebook|yelp|linkedin|reddit|"
                        r"bottlecapps\.com|liquorapps|apps\.apple|play\.google|youtube|glassdoor|indeed", re.I)


def find_stores(pages=8, log=print):
    """Discover Bottlecapps store domains nationally by SERPing the platform fingerprint (reuses off_premise's
    discover_stores — BD is used ONLY here, for discovery; the actual catalog pull is BD-free). Filters SERP noise."""
    import off_premise
    domains = [d for d in off_premise.discover_stores(_BC_SIG, pages=pages, log=log) if not _NOT_STORE.search(d)]
    log("[bottlecapps] discovery: %d candidate store domains (noise filtered)" % len(domains))
    return domains


def national(max_stores=None, max_products=None, log=print):
    """Discover every Bottlecapps store + FULL-capture each → accumulate into bottlecapps_products. This is the
    'grab everything' entry point. Runs on the Mac (headful, DataDome). Resumable via write_accumulate (key
    store|pid), so a re-run refreshes prices and adds new products without dropping prior capture.

    Each store is pulled in its OWN SUBPROCESS: the patchright/Chromium sync driver leaves an asyncio loop that
    breaks the next store's launch if reused in-process, and a fresh process also gives each store a clean browser
    (better DataDome clears) and isolates crashes. The child (`--store`) pulls + lands itself; we tally its summary."""
    import subprocess
    import glob
    stores = find_stores(log=log)
    if max_stores:
        stores = stores[:max_stores]
    here = os.path.dirname(os.path.abspath(__file__))
    total, ok = 0, 0
    for i, base in enumerate(stores):
        base = base if base.startswith("http") else "https://" + base
        log("[bottlecapps] store %d/%d: %s" % (i + 1, len(stores), base))
        for lk in glob.glob(os.path.expanduser("~/.hoodie_browser_profiles/*/Singleton*")):
            try:
                os.remove(lk)                                # clear stale profile locks so the browser can launch
            except Exception:
                pass
        args = ["--store", base] + (["--max-products", str(max_products)] if max_products else [])
        code = ("import sys; sys.path.insert(0, %r); import kroger_api; kroger_api._load_creds(); "
                "import bottlecapps; bottlecapps.main(%r)" % (here, args))
        try:
            r = subprocess.run([sys.executable, "-c", code], cwd=here, timeout=7200,
                               capture_output=True, text=True)
            m = re.search(r'"products":\s*(\d+)', r.stdout or "")
            n = int(m.group(1)) if m else 0
            if n:
                total += n
                ok += 1
            log("  → %d products%s" % (n, "" if n else " (0 — %s)" % ((r.stderr or "").strip().splitlines()[-1][:70] if r.stderr else "no summary")))
        except subprocess.TimeoutExpired:
            log("  %s TIMEOUT (>2h)" % base)
        except Exception as e:
            log("  %s ERR %s" % (base, str(e)[:90]))
    log("[bottlecapps] NATIONAL done: %d products across %d/%d stores" % (total, ok, len(stores)))
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description="Full-capture Bottlecapps scraper (no Bright Data).")
    ap.add_argument("--store", help="a Bottlecapps store base URL, e.g. https://www.douglaswine.com")
    ap.add_argument("--national", action="store_true", help="discover ALL Bottlecapps stores + full-capture each")
    ap.add_argument("--max-stores", type=int, default=0, help="cap stores in --national (0 = all)")
    ap.add_argument("--max-products", type=int, default=0, help="cap products/store (0 = all)")
    ap.add_argument("--no-detail", action="store_true", help="discover product URLs only (skip per-product fetch)")
    ap.add_argument("--no-land", action="store_true")
    a = ap.parse_args(argv)
    if a.national:
        n = national(max_stores=(a.max_stores or None), max_products=(a.max_products or None))
        print(json.dumps({"mode": "national", "products": n}))
        return 0
    if not a.store:
        print("need --store <url>  (or --national)"); return 2
    recs = pull_store(a.store, max_products=(a.max_products or None), detail=not a.no_detail)
    if recs and not a.no_land:
        try:
            land(recs)
        except Exception as e:
            print("land skipped:", str(e)[:100])
    print(json.dumps({"store": a.store, "products": len(recs),
                      "with_upc": sum(1 for r in recs if r.get("upc")),
                      "priced": sum(1 for r in recs if r.get("price")),
                      "sample": recs[:3]}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
