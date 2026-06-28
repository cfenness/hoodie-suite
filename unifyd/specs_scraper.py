#!/usr/bin/env python3
"""specs_scraper.py — directional price/availability tracker for Spec's (specsonline.com).

Spec's is a ~50-store Texas chain on a Next.js storefront: prices render client-side, so a
plain fetch sees no price. We fetch each product page through Bright Data (JS executed) and
read the standard schema.org **Product JSON-LD** (name, sku, offers.price, availability) —
cleaner and less fragile than markup scraping.

Same shape as the ABC tracker: harvest product URLs from the sitemap (reachable directly),
poll a DETERMINISTIC sample each run, diff against the prior snapshot → price moves /
out-of-stock ↔ restock / assortment churn. No numeric quantity is exposed (none of these
chains publish it), so the signal is directional.

connId: `specs`. Requires Bright Data (`BRIGHTDATA_API_KEY` or a logged-in `bdata` CLI).
First run is live to confirm the JSON-LD parse, like ABC/TTB.
"""
import argparse, hashlib, json, os, re, sys, time, urllib.request
import brightdata
from abc_fws_scraper import diff_snapshots   # generic price/stock diff (keys are strings)

BASE       = "https://specsonline.com"
SITEMAP    = BASE + "/sitemap.xml"
DELAY      = float(os.environ.get("SPECS_DELAY", "1"))   # between Bright Data fetches (they're slow anyway)
SNAP_FILE  = "specs_snapshot.json"
UA         = os.environ.get("SPECS_UA", "HoodieSuite-Research/1.0 (+market-intelligence; respects robots)")
LOC_RE     = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.I)
LD_RE      = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def _http(url, timeout=25):
    """Plain fetch for the sitemaps (not bot-walled — saves Bright Data credits)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def harvest_ids(max_products=4000, log=print):
    """sitemap index → product child sitemaps → [(slug, url)]. slug (last path segment) is
    the stable key for sampling + diffing; the JSON-LD sku is captured as metadata."""
    out, seen = [], set()
    try:
        idx = _http(SITEMAP)
    except Exception as e:
        log(f"sitemap index: {e}"); return out
    children = [u for u in LOC_RE.findall(idx) if "product" in u.lower()]
    for cs in children:
        try:
            body = _http(cs)
        except Exception as e:
            log(f"sitemap {cs}: {e}"); continue
        for u in LOC_RE.findall(body):
            slug = u.rstrip("/").rsplit("/", 1)[-1]
            if slug and slug not in seen:
                seen.add(slug); out.append((slug, u))
                if len(out) >= max_products:
                    log(f"harvested {len(out)} (capped)"); return out
        log(f"{cs.rsplit('/',1)[-1]}: {len(out)} products so far")
    return out


def pick_sample(catalog, n):
    """Deterministic spread sample by slug (same products every run, for clean diffs)."""
    if n <= 0 or n >= len(catalog):
        return list(catalog)
    cat = sorted(catalog, key=lambda t: t[0])
    stride = len(cat) / float(n)
    return [cat[int(i * stride)] for i in range(n)]


def parse_product(html):
    """Pull price + availability from the schema.org Product JSON-LD. Returns (rec, ok)
    where ok=False means no price was found (parse drift → self-report degraded)."""
    price = instock = sku = name = None
    for blk in LD_RE.findall(html):
        try:
            data = json.loads(blk)
        except Exception:
            continue
        for d in (data if isinstance(data, list) else [data]):
            if not isinstance(d, dict):
                continue
            if d.get("@type") == "Product" or "offers" in d:
                offers = d.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                p = offers.get("price")
                if p is not None:
                    try: price = float(str(p).replace(",", "").replace("$", ""))
                    except ValueError: pass
                av = str(offers.get("availability") or "").lower()
                if "instock" in av:
                    instock = True
                elif "outofstock" in av or "soldout" in av:
                    instock = False
                sku = d.get("sku") or sku
                name = d.get("name") or name
    return {"price": price, "instock": instock, "sku": sku, "name": name}, price is not None


def fetch_product(slug, url, log=print):
    html = brightdata.fetch(url, "html")
    rec, ok = parse_product(html)
    rec.update({"slug": slug, "url": url, "ts": int(time.time() * 1000)})
    rec["fp"] = hashlib.sha1(f"{rec['price']}|{rec['instock']}".encode()).hexdigest()[:12]
    return rec, ok


def run_record(movement, status, warnings):
    rid = "R-SPX" + hashlib.sha1(str(movement.get("sampled", "")).encode()).hexdigest()[:3].upper()
    n = movement["sampled"]
    return {"id": rid, "connId": "specs", "startedAt": 0, "finishedAt": 0, "durationMs": 0,
            "status": status, "trigger": "manual", "total": n,
            "degraded": status == "degraded", "warnings": warnings, "healed": [],
            "extracts": [{"id": "specs_catalog", "rows": n, "delta": movement["changed"], "status": status}],
            "movement": movement}


def pull(sample=40, crawl_all=False, limit=None, out=".", state_dir=None, log=print):
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    if not brightdata.enabled():
        run = run_record(diff_snapshots({}, {}), "failed",
                         ["Bright Data not configured — set BRIGHTDATA_API_KEY or run `bdata login`."])
        return {}, [run], run["movement"]
    catalog = harvest_ids(log=log)
    if not catalog:
        run = run_record(diff_snapshots({}, {}), "failed", ["No products found in sitemap."])
        return {}, [run], run["movement"]
    targets = (catalog if limit is None else catalog[:limit]) if crawl_all else pick_sample(catalog, sample)
    log(f"catalog {len(catalog)} products; fetching {len(targets)} via Bright Data")

    cur, ok_n = {}, 0
    for i, (slug, url) in enumerate(targets):
        try:
            rec, ok = fetch_product(slug, url, log=log)
            cur[slug] = rec; ok_n += ok
        except Exception as e:
            log(f"  {slug}: {e}")
        if i < len(targets) - 1:
            time.sleep(DELAY)

    prev = {}
    snap_path = os.path.join(state_dir, SNAP_FILE)
    if os.path.exists(snap_path):
        try: prev = json.load(open(snap_path)).get("skus", {})
        except Exception: prev = {}
    movement = diff_snapshots(prev, cur)

    warnings = []
    if cur and ok_n / len(cur) < 0.5:
        warnings.append(f"Price parsed on only {ok_n}/{len(cur)} pages — confirm the Product "
                        "JSON-LD selector against a live page (Spec's storefront may have changed).")
    status = "failed" if not cur else ("degraded" if warnings else "success")

    json.dump({"__ts__": int(time.time() * 1000), "skus": cur}, open(snap_path, "w"), indent=2)
    header = ["SKU", "Product", "Price", "In Stock", "URL"]
    rows = [[r.get("sku"), r.get("name"), r.get("price"), r.get("instock"), r.get("url")]
            for s, r in sorted(cur.items())]
    datasets = {"specs_catalog": {"header": header, "rows": rows[:600], "total": len(rows), "movement": movement}}
    json.dump(datasets, open(os.path.join(out, "datasets.json"), "w"), indent=2)
    run = run_record(movement, status, warnings)
    log(f"done: {len(cur)} sampled, {movement['changed']} changed since last run"
        + ("" if prev else " (baseline)"))
    return datasets, [run], movement


def main(argv=None):
    ap = argparse.ArgumentParser(description="Directional price/availability tracker for Spec's (via Bright Data).")
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="./specs_out")
    a = ap.parse_args(argv)
    _, runs, _ = pull(sample=a.sample, crawl_all=a.all, limit=a.limit, out=a.out, state_dir=a.out)
    print(json.dumps(runs[0], indent=2))
    return 0 if runs[0]["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
