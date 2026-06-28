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
import argparse, hashlib, json, os, re, sys, time, urllib.request, urllib.error

BASE        = "https://abcfws.com"
SITEMAP     = BASE + "/xmlsitemap.php?type=products&page={}"
UA          = os.environ.get("ABC_UA",
                "HoodieSuite-Research/1.0 (+market-intelligence; respects robots, 10s delay)")
DELAY       = float(os.environ.get("ABC_DELAY", "10"))   # robots crawl-delay for our UA
SNAP_FILE   = "abc_snapshot.json"                        # latest snapshot, for day-over-day diff
PRICE_RE    = re.compile(r'(?:og:price:amount|product:price:amount)"[^>]*content="([\d.]+)"', re.I)
PRICE_TXT   = re.compile(r'\$\s?(\d[\d,]*\.\d{2})')
UPC_RE      = re.compile(r'UPC[^0-9]{0,12}(\d{8,14})', re.I)
LOC_RE      = re.compile(r'<loc>\s*([^<]+?)\s*</loc>', re.I)
ID_RE       = re.compile(r'/(\d+)/?$')


# ---------------- fetch (stdlib, identifies itself, no gzip surprises) ----------------
def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Encoding": "identity",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
        h = r.headers
        return body, {"etag": h.get("ETag", ""), "last_modified": h.get("Last-Modified", ""),
                      "status": r.status if hasattr(r, "status") else 200}


# ---------------- sitemap → stable (sku, url) catalog ----------------
def harvest_ids(max_pages=4, log=print):
    """Walk the product sitemaps, returning [(sku, url)] with sku = the trailing id.
    Stops at the first empty/missing page."""
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
def parse_product(html):
    """Pull price + availability from a BigCommerce product page. Returns
    (record, ok) where ok=False means we couldn't find a price (selector drift)."""
    m = PRICE_RE.search(html) or PRICE_TXT.search(html)
    price = None
    if m:
        try: price = float(m.group(1).replace(",", ""))
        except ValueError: price = None
    low = html.lower()
    if any(s in low for s in ("out of stock", "outofstock", "sold out", "out-of-stock")):
        instock = False
    elif any(s in low for s in ("add to cart", "addtocart", "add-to-cart")):
        instock = True
    else:
        instock = None
    um = UPC_RE.search(html)
    return {"price": price, "instock": instock, "upc": um.group(1) if um else None}, price is not None


def fetch_product(sku, url, log=print):
    body, hdr = fetch(url)
    rec, ok = parse_product(body)
    rec.update({"sku": sku, "url": url, "etag": hdr["etag"], "last_modified": hdr["last_modified"],
                "ts": int(time.time() * 1000)})
    # fingerprint of the *parsed* fields (ignores per-request CSRF/session noise in the raw HTML)
    rec["fp"] = hashlib.sha1(f"{rec['price']}|{rec['instock']}|{rec['upc']}".encode()).hexdigest()[:12]
    return rec, ok


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
def run_record(snapshot, movement, status, warnings):
    rid = "R-ABC" + hashlib.sha1(str(snapshot.get("__ts__", "")).encode()).hexdigest()[:3].upper()
    n = movement["sampled"]
    return {"id": rid, "connId": "abc-fws", "startedAt": 0, "finishedAt": 0, "durationMs": 0,
            "status": status, "trigger": "manual", "total": n,
            "degraded": status == "degraded", "warnings": warnings, "healed": [],
            "extracts": [{"id": "abc_catalog", "rows": n,
                          "delta": movement["changed"], "status": status}],
            "movement": movement}


def pull(sample=40, crawl_all=False, limit=None, out=".", state_dir=None, log=print):
    """One run: harvest ids → fetch (sample or all) → diff vs prior snapshot → persist.
    Returns (datasets, [run_record], movement)."""
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    catalog = harvest_ids(log=log)
    if not catalog:
        run = run_record({}, diff_snapshots({}, {}), "failed",
                         ["No products found in sitemap — site structure may have changed."])
        return {}, [run], run["movement"]

    targets = (catalog if limit is None else catalog[:limit]) if crawl_all else pick_sample(catalog, sample)
    log(f"catalog {len(catalog)} products; pulling {len(targets)} (delay {DELAY}s) ~{int(len(targets)*DELAY)}s")

    cur, ok_n = {}, 0
    for i, (sku, url) in enumerate(targets):
        try:
            rec, ok = fetch_product(sku, url, log=log)
            cur[sku] = rec; ok_n += ok
        except Exception as e:
            log(f"  {sku}: {e}")
        if i < len(targets) - 1:
            time.sleep(DELAY)

    # load the previous snapshot (written by the last run) and diff
    prev = {}
    snap_path = os.path.join(state_dir, SNAP_FILE)
    if os.path.exists(snap_path):
        try: prev = json.load(open(snap_path)).get("skus", {})
        except Exception: prev = {}
    movement = diff_snapshots(prev, cur)

    # self-report: if we couldn't read a price on most pages, the selector drifted
    warnings = []
    if cur and ok_n / len(cur) < 0.5:
        warnings.append(f"Price parsed on only {ok_n}/{len(cur)} pages — confirm selectors "
                        "against a live product page (BigCommerce theme may have changed).")
    status = "failed" if not cur else ("degraded" if warnings else "success")

    # persist the new snapshot for next run's diff, and emit a browsable dataset
    json.dump({"__ts__": int(time.time() * 1000), "skus": cur},
              open(snap_path, "w"), indent=2)
    header = ["SKU", "Price", "In Stock", "UPC", "URL"]
    rows = [[s, r.get("price"), r.get("instock"), r.get("upc"), r.get("url")]
            for s, r in sorted(cur.items(), key=lambda kv: int(kv[0]))]
    datasets = {"abc_catalog": {"header": header, "rows": rows[:600],
                                "total": len(rows), "movement": movement}}
    json.dump(datasets, open(os.path.join(out, "datasets.json"), "w"), indent=2)
    snapshot = {"__ts__": int(time.time() * 1000), "skus": cur}
    run = run_record(snapshot, movement, status, warnings)
    log(f"done: {len(cur)} sampled, {movement['changed']} changed since last run"
        + (f", {len(movement['new'])} new / {len(movement['dropped'])} dropped" if prev else " (no prior snapshot — baseline)"))
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
