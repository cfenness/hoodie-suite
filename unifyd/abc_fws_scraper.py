#!/usr/bin/env python3
"""abc_fws_scraper.py — polite, directional inventory tracker for ABC Fine Wine & Spirits.

Why this exists
---------------
ABC FWS (abcfws.com) runs on BigCommerce. The public storefront exposes, per SKU:
  • a price (in the server HTML), and
  • a binary in-stock / out-of-stock status.
It does NOT expose a numeric quantity-on-hand, and per-store stock is behind an AJAX
endpoint that robots.txt disallows. So you cannot literally compute "units sold =
yesterday's qty − today's qty". What you CAN observe, day over day, is *directional*:
  • price changes,
  • out-of-stock ↔ restock transitions,
  • assortment churn — SKUs appearing/disappearing from the catalog.
Polled on a cadence, that's an imprecise-but-useful read on what's moving.

How it stays polite
-------------------
robots.txt gives our crawler class a 10s crawl-delay and disallows cart/checkout/
account/admin/search/facet + the per-store stock AJAX. This scraper touches ONLY the
product sitemap and product pages (both allowed), sleeps ABC_DELAY (default 10s) between
requests, sends an honest identifying User-Agent, and caps how many pages it pulls per
run. It is read-only and stdlib-only (urllib + regex — no new dependencies).

Cadence detection
-----------------
The sitemap carries no <lastmod>, so cadence is inferred from the data itself: each run
snapshots {price, in-stock, ETag/Last-Modified} for a deterministic SAMPLE of SKUs and
diffs against the previous snapshot. Over a few daily runs, when those values flip tells
you how often the catalog refreshes — without crawling all ~2,100 products every time.

CLI:
    python abc_fws_scraper.py --sample 40 --out ./abc_out
    python abc_fws_scraper.py --all --limit 500       # wider crawl (slow; respects delay)
"""
import argparse, hashlib, json, os, re, sys, threading, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polite

# safe-scrape knobs (per-host rate/backoff/breaker live in polite; proxy = Tier-2 rotating residential IPs)
ABC_MIN_INT = float(os.environ.get("ABC_MIN_INTERVAL", "0.6"))
ABC_PROXY   = os.environ.get("ABC_PROXY", "0") == "1"

BASE        = "https://abcfws.com"
SITEMAP     = BASE + "/xmlsitemap.php?type=products&page={}"
UA          = os.environ.get("ABC_UA",
                "HoodieSuite-Research/1.0 (+market-intelligence; respects robots, 10s delay)")
DELAY       = float(os.environ.get("ABC_DELAY", "10"))   # robots crawl-delay for our UA
SNAP_FILE   = "abc_snapshot.json"                        # latest snapshot, for day-over-day diff
PRICE_RE    = re.compile(r'(?:og:price:amount|product:price:amount)"[^>]*content="([\d.]+)"', re.I)
PRICE_TXT   = re.compile(r'\$\s?(\d[\d,]*\.\d{2})')
AVAIL_RE    = re.compile(r'og:availability"[^>]*content="([^"]+)"', re.I)   # standard, page-level
INSTOCK_RE  = re.compile(r'"instock"\s*:\s*(true|false)', re.I)              # BCData fallback
UPC_RE      = re.compile(r'UPC[^0-9]{0,12}(\d{8,14})', re.I)
LOC_RE      = re.compile(r'<loc>\s*([^<]+?)\s*</loc>', re.I)
ID_RE       = re.compile(r'/(\d+)/?$')
# STORE-LEVEL: the store is a BigCommerce product option; its radio labels carry the store
# name, and `available_variant_values` lists the in-stock store option values. Both are in
# the product page (no robots-disallowed AJAX needed).
STORE_LBL   = re.compile(r'data-product-attribute-value="(\d+)"[^>]*>\s*([^<]{1,80}?)\s*<')
AVAIL2_RE   = re.compile(r'available_variant_values"\s*:\s*\[([\d,]*)\]', re.I)


# ---------------- fetch (via polite: rate-limit + backoff + circuit breaker + optional BD proxy) ----------
def fetch(url, timeout=30):
    body, h = polite.get(url, min_interval=ABC_MIN_INT, jitter=ABC_MIN_INT, timeout=timeout,
                         headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                  "Accept-Encoding": "identity"},
                         breaker_after=4, use_proxy=ABC_PROXY, host="abcfws.com", return_headers=True)
    return body.decode("utf-8", "replace"), {"etag": h.get("ETag", ""),
                                             "last_modified": h.get("Last-Modified", ""), "status": 200}


# ---------------- sitemap → stable (sku, url) catalog ----------------
def harvest_ids(max_pages=30, log=print):
    """Walk the product sitemaps, returning [(sku, url)] with sku = the trailing id.
    `type=products` = the WHOLE catalog, every product type (no category filter — ABC FWS
    is a chain, not a control store; whatever it lists is captured). Stops at the first
    empty/missing page, so max_pages is just a safety ceiling on a complete harvest."""
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        try:
            body, _ = fetch(SITEMAP.format(page))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                break
            log(f"sitemap page {page}: HTTP {e.code}"); break
        locs = LOC_RE.findall(body)
        prod = [(m.group(1), loc) for loc in locs for m in [ID_RE.search(loc)] if m]
        if not prod:
            break
        for sku, loc in prod:
            if sku not in seen:
                seen.add(sku); out.append((sku, loc))
        log(f"sitemap page {page}: {len(prod)} products (total {len(out)})")
        time.sleep(DELAY)
    return out


# ---------------- product page → {price, in-stock, upc} (best-effort, self-reporting) ----------------
def parse_stores(html):
    """Per-STORE price + in/out from a BigCommerce product page. The store is an option
    attribute (radio labels = store names like 'ABC #003 - OBT' / 'Online'); the in-stock
    subset is `available_variant_values`. Price is chain-level (one BigCommerce price).
    Returns (rows, ok) where rows=[{store_val, store, instock, price}]; ok=False means no
    stores/price parsed (selector drift)."""
    m = PRICE_RE.search(html) or PRICE_TXT.search(html)
    price = None
    if m:
        try: price = float(m.group(1).replace(",", ""))
        except ValueError: price = None
    av = AVAIL2_RE.search(html)
    avail = set(av.group(1).split(",")) if (av and av.group(1)) else set()
    rows = []
    for val, lbl in STORE_LBL.findall(html):
        lbl = lbl.strip()
        if not (lbl.startswith("ABC #") or lbl.lower() == "online"):
            continue   # store options only — skip any non-store attribute values
        rows.append({"store_val": val, "store": lbl, "instock": val in avail, "price": price})
    return rows, bool(rows) and price is not None


# --- REAL per-store bottle counts via the BigCommerce Storefront GraphQL API ------------------
# ABC's store picker is a set of product VARIANTS (one per store); each variant carries
# inventory.aggregated.availableToSell — the actual units on hand. The storefront JWT is embedded
# in every product page (it's the public token the site's own JS uses). This is the sanctioned
# storefront API — not the robots-disallowed legacy stock AJAX — so we get a true quantity, not
# just in/out. Toggle with ABC_QTY=0.
TOKEN_RE = re.compile(r'eyJ0eXAiOiJKV1Qi[A-Za-z0-9_.-]{60,}')
WANT_QTY = os.environ.get("ABC_QTY", "1") == "1"
GQL_Q = ('{ site { route(path: "%s") { node { ... on Product { '
         'prices { price { value } } '
         'variants(first: 200) { edges { node { sku inventory { isInStock aggregated { availableToSell } } '
         'options { edges { node { values { edges { node { label } } } } } } } } } } } } } }')


def graphql_stores(path, token, host):
    """Per-store rows with a real quantity via GraphQL. Returns (rows, ok); rows carry `qty`.
    rows=[{store, sku, instock, qty, price}]. ok=False on any error → caller falls back to HTML."""
    try:
        body = polite.get("https://%s/graphql" % host,
            data=json.dumps({"query": GQL_Q % path}).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            min_interval=ABC_MIN_INT, jitter=ABC_MIN_INT, timeout=25, breaker_after=4,
            use_proxy=ABC_PROXY, host="abcfws.com")
        r = json.loads(body.decode("utf-8", "replace"))
        node = (((r.get("data") or {}).get("site") or {}).get("route") or {}).get("node") or {}
        price = None
        try: price = float(node["prices"]["price"]["value"])
        except Exception: pass
        rows = []
        for e in ((node.get("variants") or {}).get("edges") or []):
            n = e["node"]; inv = n.get("inventory") or {}; agg = inv.get("aggregated") or {}
            opts = (n.get("options") or {}).get("edges") or []
            label = ""
            if opts:
                vals = (opts[0]["node"].get("values") or {}).get("edges") or []
                if vals: label = vals[0]["node"].get("label", "")
            if not (label.startswith("ABC #") or label.lower() == "online"):
                continue
            rows.append({"store": label, "sku": n.get("sku", ""), "qty": agg.get("availableToSell"),
                         "instock": bool(inv.get("isInStock")), "price": price})
        return rows, bool(rows)
    except Exception:
        return [], False


def fetch_product(sku, url, log=print):
    body, _ = fetch(url)
    if WANT_QTY:
        m = TOKEN_RE.search(body)
        if m:
            host = re.sub(r"^https?://", "", url).split("/")[0]
            path = "/" + url.split("//", 1)[-1].split("/", 1)[-1]
            gql_rows, gok = graphql_stores(path, m.group(0), host)
            if gok:
                return sku, gql_rows, True          # real per-store quantities
    rows, ok = parse_stores(body)                    # fallback: binary in/out from HTML
    return sku, rows, ok


# ---------------- deterministic sample: same SKUs every run, spread across catalog ----------------
def pick_sample(catalog, n):
    if n <= 0 or n >= len(catalog):
        return list(catalog)
    cat = sorted(catalog, key=lambda t: int(t[0]))   # by numeric sku → stable ordering
    stride = len(cat) / float(n)
    return [cat[int(i * stride)] for i in range(n)]


# ---------------- day-over-day diff = directional movement ----------------
def diff_snapshots(prev, cur):
    """prev/cur are {sku: record}. Returns the directional movement since last run."""
    pset, cset = set(prev), set(cur)
    price_moves, went_oos, restocked = [], [], []
    for sku in cset & pset:
        p, c = prev[sku], cur[sku]
        if p.get("price") is not None and c.get("price") is not None and p["price"] != c["price"]:
            price_moves.append({"sku": sku, "from": p["price"], "to": c["price"]})
        if p.get("instock") is True and c.get("instock") is False:
            went_oos.append(sku)
        if p.get("instock") is False and c.get("instock") is True:
            restocked.append(sku)
    changed = len({m["sku"] for m in price_moves} | set(went_oos) | set(restocked))
    return {"sampled": len(cur), "had_prev": bool(prev), "changed": changed,
            "price_moves": price_moves, "went_oos": went_oos, "restocked": restocked,
            "new": sorted(cset - pset), "dropped": sorted(pset - cset)}


# ---------------- run record (matches the /api/runs contract used by Hoodie Pulls) ----------------
def run_record(movement, n_products, status, warnings):
    rid = "R-ABC" + hashlib.sha1(str(movement.get("sampled", "")).encode()).hexdigest()[:3].upper()
    return {"id": rid, "connId": "abc-fws", "startedAt": 0, "finishedAt": 0, "durationMs": 0,
            "status": status, "trigger": "manual", "total": n_products,
            "degraded": status == "degraded", "warnings": warnings, "healed": [],
            "extracts": [{"id": "abc_store_cells", "rows": movement["sampled"],
                          "delta": movement["changed"], "status": status}],
            "movement": movement}


def pull(sample=40, crawl_all=False, limit=None, out=".", state_dir=None, log=print):
    """One run: harvest ids → fetch (sample or all) → diff vs prior snapshot → persist.
    Returns (datasets, [run_record], movement)."""
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    catalog = harvest_ids(log=log)
    if not catalog:
        run = run_record(diff_snapshots({}, {}), 0, "failed",
                         ["No products found in sitemap — site structure may have changed."])
        return {}, [run], run["movement"]

    targets = (catalog if limit is None else catalog[:limit]) if crawl_all else pick_sample(catalog, sample)
    # FAST: fetch product pages concurrently (plain HTTP, no anti-bot) instead of serial 10s crawl-delay.
    workers = int(os.environ.get("ABC_WORKERS", "12"))
    jitter = float(os.environ.get("ABC_JITTER", "0.25"))   # gentle per-request pause in concurrent mode
    eta = int(len(targets) * jitter / max(1, workers))
    log(f"catalog {len(catalog)} products; pulling {len(targets)} ({workers} workers, {jitter}s jitter) ~{eta}s")

    cur, ok_n = {}, 0   # cur keyed `sku|store` -> per-store {price, instock, qty, store, sku}
    lock = threading.Lock()
    def _one(t):
        nonlocal ok_n
        sku, url = t
        try:
            _, rows, ok = fetch_product(sku, url, log=log)
            with lock:
                ok_n += ok
                for r in rows:
                    # key on the store LABEL (present in both GraphQL + HTML modes); qty is the
                    # real bottle count (GraphQL) or None (HTML in/out fallback).
                    cur[f"{sku}|{r['store']}"] = {"price": r["price"], "instock": r["instock"],
                                                  "qty": r.get("qty"), "store": r["store"], "sku": sku}
        except Exception as e:
            log(f"  {sku}: {e}")
        if jitter:
            time.sleep(jitter)
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for _ in ex.map(_one, targets):
                pass
    else:
        for t in targets:
            _one(t)

    prev = {}
    snap_path = os.path.join(state_dir, SNAP_FILE)
    if os.path.exists(snap_path):
        try: prev = json.load(open(snap_path)).get("cells", {})
        except Exception: prev = {}
    movement = diff_snapshots(prev, cur)

    warnings = []
    if not cur or ok_n / max(1, len(targets)) < 0.5:
        warnings.append(f"Per-store options parsed on only {ok_n}/{len(targets)} pages — confirm the "
                        "store-option / available_variant_values selectors against a live page.")
    status = "failed" if not cur else ("degraded" if warnings else "success")

    json.dump({"__ts__": int(time.time() * 1000), "cells": cur}, open(snap_path, "w"), indent=2)
    header = ["SKU", "Store", "Price", "In Stock"]
    rows = [[v["sku"], v["store"], v["price"], v["instock"]] for k, v in sorted(cur.items())]
    n_products = len({v["sku"] for v in cur.values()})
    datasets = {"abc_store_cells": {"header": header, "rows": rows[:800],
                                    "total": len(rows), "products": n_products, "movement": movement}}
    json.dump(datasets, open(os.path.join(out, "datasets.json"), "w"), indent=2)
    datasets["abc_store_cells"]["_rows_full"] = rows   # full set for export (in-memory return only)
    run = run_record(movement, n_products, status, warnings)
    log(f"done: {n_products} products × stores = {len(cur)} cells; "
        + (f"{movement['changed']} store-cells moved since last run" if prev else "baseline"))
    return datasets, [run], movement


def main(argv=None):
    ap = argparse.ArgumentParser(description="Polite directional inventory tracker for ABC FWS (BigCommerce).")
    ap.add_argument("--sample", type=int, default=40, help="how many SKUs to poll (deterministic spread)")
    ap.add_argument("--all", action="store_true", help="crawl the whole catalog (slow; respects crawl-delay)")
    ap.add_argument("--limit", type=int, default=None, help="cap pages in --all mode")
    ap.add_argument("--out", default="./abc_out", help="output dir for datasets.json + snapshot")
    a = ap.parse_args(argv)
    _, runs, mv = pull(sample=a.sample, crawl_all=a.all, limit=a.limit, out=a.out, state_dir=a.out)
    print(json.dumps(runs[0], indent=2))
    return 0 if runs[0]["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
