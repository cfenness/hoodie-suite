"""walmart_api.py — Walmart bev-alc CATALOG via the OFFICIAL Walmart I/O Affiliate API (direct, no scraping).

walmart.com is PerimeterX-gated, so the BD `walmart_product` scraper (walmart_scraper.py) is one-URL-at-a-time
and only ever sampled ~24 products. Walmart's own developer API returns the catalog directly, paginated, at
scale — the same clean pattern as kroger_api.py. This pulls the bev-alc catalog via the Search API (up to
1,000 items/term) + optional Catalog Product API (by category), normalizes to the walmart_products shape, and
ACCUMULATES into the warehouse. NOTE: this is CATALOG level (walmart.com prices) — per-store price/stock is not
in the public API (that stays BD / a dataset feed).

Auth (signature-based, Walmart I/O): sign "<consumerId>\\n<timestampMs>\\n<keyVersion>\\n" with your RSA private
key (SHA-256, PKCS#1 v1.5) → base64 → WM_SEC.AUTH_SIGNATURE. Enroll at walmart.io (Impact Radius Publisher ID +
Consumer ID + private key). Set:
    WALMART_CONSUMER_ID, WALMART_KEY_VERSION (default 1), and WALMART_PRIVATE_KEY (PEM, \\n-escaped ok) or
    WALMART_PRIVATE_KEY_FILE (path to the .pem). WALMART_PUBLISHER_ID optional (affiliate links).

    python walmart_api.py                    # pull the default bev-alc term set
    python walmart_api.py --terms "bourbon,tequila reposado,pinot noir"
"""
import argparse, base64, json, os, re, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

API = "https://developer.api.walmart.com/api-proxy/service/affil/product/v2"

# a broad-but-focused bev-alc term set (Search API is text-based; each term paginates to ~1,000 items)
DEFAULT_TERMS = [
    "bourbon", "scotch whisky", "irish whiskey", "rye whiskey", "tennessee whiskey", "blended whiskey",
    "vodka", "flavored vodka", "gin", "london dry gin", "white rum", "spiced rum", "dark rum",
    "tequila blanco", "tequila reposado", "tequila anejo", "mezcal", "cognac", "brandy", "liqueur",
    "cabernet sauvignon", "merlot", "pinot noir", "red blend", "malbec", "zinfandel", "chardonnay",
    "sauvignon blanc", "pinot grigio", "riesling", "rose wine", "champagne", "prosecco", "sparkling wine",
    "moscato", "sangria", "port wine",
    "ipa beer", "lager beer", "pilsner", "stout beer", "wheat beer", "light beer", "craft beer",
    "hard seltzer", "hard cider", "canned cocktail", "wine cooler", "malt beverage",
]


def _load_key():
    pem = os.environ.get("WALMART_PRIVATE_KEY", "")
    if pem:
        return pem.replace("\\n", "\n").encode()
    path = os.environ.get("WALMART_PRIVATE_KEY_FILE", "")
    if path and os.path.exists(path):
        return open(path, "rb").read()
    return None


def _headers():
    """Build the signed Walmart I/O auth headers. Raises if creds/lib missing (so the run reports it clearly)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    consumer_id = os.environ.get("WALMART_CONSUMER_ID", "")
    key_version = os.environ.get("WALMART_KEY_VERSION", "1")
    pem = _load_key()
    if not (consumer_id and pem):
        raise RuntimeError("Walmart I/O creds missing — set WALMART_CONSUMER_ID + WALMART_PRIVATE_KEY(_FILE) "
                           "(and WALMART_KEY_VERSION). Enroll at walmart.io.")
    ts = str(int(time.time() * 1000))
    msg = ("%s\n%s\n%s\n" % (consumer_id, ts, key_version)).encode()
    key = serialization.load_pem_private_key(pem, password=None)
    sig = base64.b64encode(key.sign(msg, padding.PKCS1v15(), hashes.SHA256())).decode()
    h = {"WM_CONSUMER.ID": consumer_id, "WM_CONSUMER.INTIMESTAMP": ts,
         "WM_SEC.KEY_VERSION": key_version, "WM_SEC.AUTH_SIGNATURE": sig, "Accept": "application/json"}
    pub = os.environ.get("WALMART_PUBLISHER_ID")
    if pub:
        h["WM_CONSUMER.IMP.ID"] = pub
    return h


def _req(path, log=print, tries=4):
    """GET the API (fresh signature per call — the timestamp has a 180s TTL). Backs off on 429/5xx."""
    url = path if path.startswith("http") else (API + path)
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers=_headers())
            return json.loads(urllib.request.urlopen(r, timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < tries - 1:
                time.sleep(2 ** i); continue
            log("  [walmart-api] HTTP %s on %s" % (e.code, url[:80])); raise
    return {}


_SZ = re.compile(r"([\d.]+)\s*(ml|l|liter|litre|oz|fl oz)", re.I)


def _size_ml(*vals):
    for v in vals:
        m = _SZ.search(str(v or ""))
        if m:
            n, u = float(m.group(1)), m.group(2).lower()
            return round(n * (1000 if u.startswith(("l", "lit")) else (29.5735 if "oz" in u else 1)))
    return None


def _norm(it, run_id):
    name = it.get("name", "")
    sale, msrp = it.get("salePrice"), it.get("msrp")
    stock = it.get("stock", "")
    return dict(
        run_id=run_id, product_id=str(it.get("itemId", "")), product_name=name,
        brand=it.get("brandName", ""), category=it.get("categoryPath", ""), is_alcohol=True,
        size_ml=_size_ml(it.get("size"), name), abv=None, upc=it.get("upc", ""),
        price=sale, list_price=msrp, on_promo=bool(sale and msrp and sale < msrp),
        availability=stock, in_stock=(stock == "Available" or bool(it.get("availableOnline"))),
        store="walmart.com", store_loc="", rating=it.get("customerRating"), reviews=it.get("numReviews"),
        url=it.get("productUrl") or it.get("productTrackingUrl", ""), pulled_at=int(time.time()))


def search_term(term, run_id, max_items=1000, log=print):
    """Search API — paginated (numItems max 25, start 1-based up to ~1000)."""
    out, start = [], 1
    while start <= max_items:
        q = urllib.parse.urlencode({"query": term, "numItems": 25, "start": start})
        d = _req("/search?" + q, log=log)
        items = d.get("items") or []
        out.extend(_norm(it, run_id) for it in items)
        if len(items) < 25:
            break
        start += 25
        time.sleep(float(os.environ.get("WALMART_PACE", "0.4")))
    return out


def run(terms=None, max_per_term=1000, log=print):
    terms = terms or DEFAULT_TERMS
    run_id = "wmapi-" + time.strftime("%Y%m%d-%H%M%S")
    try:
        _headers()                                   # fail fast + clearly if creds/lib missing
    except Exception as e:
        log("  [walmart-api] OFF — %s" % e)
        return None
    seen, rows = set(), []
    for i, t in enumerate(terms, 1):
        try:
            got = search_term(t, run_id, max_items=max_per_term, log=log)
        except Exception as e:
            log("  [walmart-api] term '%s' failed: %s" % (t, str(e)[:70])); continue
        new = 0
        for r in got:
            k = r["product_id"] or r["upc"] or r["url"]
            if k and k not in seen:
                seen.add(k); rows.append(r); new += 1
        log("  [walmart-api] %-22s +%d (%d total)" % (t[:22], new, len(rows)))
    if not rows:
        log("[walmart-api] no products (check creds / term set)"); return run_id, 0
    # ACCUMULATE into walmart_products (shared with the BD path) + dated observations + a run row
    warehouse.write_accumulate("walmart_products", rows, key=lambda r: r.get("product_id") or r.get("upc") or r.get("url"))
    observe.record("walmart", [dict(store=r["store"], store_id="", product_id=r["product_id"], upc=r["upc"],
                                    brand=r["brand"], name=r["product_name"], price=r["price"], promo=None,
                                    on_promo=r["on_promo"], in_stock=r["in_stock"], qty=None,
                                    stock_level=r["availability"], is_hemp=False) for r in rows])
    runs = []
    try:
        runs = warehouse.query("walmart_runs", "SELECT * FROM t")
    except Exception:
        pass
    runs.append(dict(run_id=run_id, at=int(time.time()), products=len(rows),
                     in_stock=sum(1 for r in rows if r["in_stock"]), concerns=0, high_concerns=0,
                     status="ok", note="walmart.io catalog api", warnings="[]"))
    warehouse.write_parquet("walmart_runs", runs[-50:])
    log("[walmart-api] DONE %d catalog products across %d terms -> walmart_products" % (len(rows), len(terms)))
    return run_id, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", default="")
    ap.add_argument("--max-per-term", type=int, default=1000)
    a = ap.parse_args()
    terms = [t.strip() for t in a.terms.split(",") if t.strip()] or None
    run(terms, max_per_term=a.max_per_term)


if __name__ == "__main__":
    main()
