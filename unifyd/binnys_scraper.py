#!/usr/bin/env python3
"""binnys_scraper.py — directional price/availability tracker for Binny's (binnys.com).

Binny's powers its catalog with Algolia, and Algolia search keys are public by design
(every Algolia storefront ships one to the browser). So instead of scraping React pages we
query the index directly — a clean JSON feed with price + sold-out + per-store stock counts.
No Bright Data needed; this is the same call the site's own search box makes.

connId: `binnys`. Richest signal of the chains: onlineStoreBestPrice, isSoldOut, and
inStockStores (how many stores carry it) — so the day-over-day diff catches price moves,
sold-out/restock, store-count churn, and assortment changes.

The app id / index / search key are read from env (defaults are the values discovered on
binnys.com). If Algolia ever rejects the key, the run self-reports `degraded` with a note
to re-discover it from a product page.
"""
import argparse, hashlib, json, os, sys, time, urllib.request
from abc_fws_scraper import diff_snapshots   # generic price/stock diff (string keys)

APP_ID  = os.environ.get("BINNYS_ALGOLIA_APP", "Z25A2A928M")
API_KEY = os.environ.get("BINNYS_ALGOLIA_KEY", "88b6125855a0bbd845447e35de8d51c5")  # public search-only key
INDEX   = os.environ.get("BINNYS_ALGOLIA_INDEX", "Products_Production")
HOST    = f"https://{APP_ID}-dsn.algolia.net/1/indexes/{INDEX}/query"
SNAP    = "binnys_snapshot.json"


def query(page=0, hits=1000):
    body = json.dumps({"params": f"query=&hitsPerPage={hits}&page={page}"}).encode()
    req = urllib.request.Request(HOST, data=body, method="POST", headers={
        "X-Algolia-Application-Id": APP_ID, "X-Algolia-API-Key": API_KEY,
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_records(sample=300, crawl_all=False, log=print):
    if not crawl_all:
        return query(0, min(sample, 1000)).get("hits", [])
    first = query(0, 1000)
    out = list(first.get("hits", []))
    pages = min(first.get("nbPages", 1), 40)   # Algolia caps deep paging; 40k products plenty
    for p in range(1, pages):
        try:
            out += query(p, 1000).get("hits", [])
        except Exception as e:
            log(f"page {p}: {e}"); break
        log(f"page {p + 1}/{pages}: {len(out)} records")
    return out


def to_snapshot(records):
    snap = {}
    for h in records:
        oid = str(h.get("objectID") or "")
        if not oid:
            continue
        p = h.get("onlineStoreBestPrice")
        try: price = float(p) if p is not None else None
        except (ValueError, TypeError): price = None
        stores = h.get("inStockStores")
        snap[oid] = {
            "price": price,
            "instock": (not h.get("isSoldOut")) if "isSoldOut" in h else None,
            "stores": len(stores) if isinstance(stores, list) else None,
            "name": h.get("productName"), "url": h.get("productUrl"),
            "fp": hashlib.sha1(f"{price}|{h.get('isSoldOut')}".encode()).hexdigest()[:12],
        }
    return snap


def run_record(movement, status, warnings):
    rid = "R-BIN" + hashlib.sha1(str(movement.get("sampled", "")).encode()).hexdigest()[:3].upper()
    n = movement["sampled"]
    return {"id": rid, "connId": "binnys", "startedAt": 0, "finishedAt": 0, "durationMs": 0,
            "status": status, "trigger": "manual", "total": n,
            "degraded": status == "degraded", "warnings": warnings, "healed": [],
            "extracts": [{"id": "binnys_catalog", "rows": n, "delta": movement["changed"], "status": status}],
            "movement": movement}


def pull(sample=300, crawl_all=False, limit=None, out=".", state_dir=None, log=print):
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    try:
        records = fetch_records(sample=sample, crawl_all=crawl_all, log=log)
    except Exception as e:
        run = run_record(diff_snapshots({}, {}), "failed",
                         [f"Algolia query failed: {str(e)[:120]} — the public search key may have "
                          "rotated; re-discover it from a binnys.com product page."])
        return {}, [run], run["movement"]
    if limit:
        records = records[:limit]
    cur = to_snapshot(records)
    if not cur:
        run = run_record(diff_snapshots({}, {}), "failed", ["Algolia returned 0 products (index/key changed)."])
        return {}, [run], run["movement"]

    prev = {}
    snap_path = os.path.join(state_dir, SNAP)
    if os.path.exists(snap_path):
        try: prev = json.load(open(snap_path)).get("skus", {})
        except Exception: prev = {}
    movement = diff_snapshots(prev, cur)

    priced = sum(1 for v in cur.values() if v["price"] is not None)
    warnings = []
    if priced / len(cur) < 0.5:
        warnings.append(f"Price present on only {priced}/{len(cur)} records — Algolia schema may have changed.")
    status = "degraded" if warnings else "success"

    json.dump({"__ts__": int(time.time() * 1000), "skus": cur}, open(snap_path, "w"), indent=2)
    header = ["SKU", "Product", "Price", "In Stock", "# Stores", "URL"]
    rows = [[k, v["name"], v["price"], v["instock"], v["stores"], v["url"]] for k, v in sorted(cur.items())]
    datasets = {"binnys_catalog": {"header": header, "rows": rows[:600], "total": len(rows), "movement": movement}}
    json.dump(datasets, open(os.path.join(out, "datasets.json"), "w"), indent=2)
    run = run_record(movement, status, warnings)
    log(f"done: {len(cur)} products, {movement['changed']} changed since last run" + ("" if prev else " (baseline)"))
    return datasets, [run], movement


def main(argv=None):
    ap = argparse.ArgumentParser(description="Directional price/availability tracker for Binny's (Algolia feed).")
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--all", action="store_true", help="paginate the whole index (~31k products)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="./binnys_out")
    a = ap.parse_args(argv)
    _, runs, _ = pull(sample=a.sample, crawl_all=a.all, limit=a.limit, out=a.out, state_dir=a.out)
    print(json.dumps(runs[0], indent=2))
    return 0 if runs[0]["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
