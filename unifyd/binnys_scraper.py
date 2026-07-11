#!/usr/bin/env python3
"""binnys_scraper.py — STORE-LEVEL price + inventory tracker for Binny's (binnys.com).

Binny's runs on Algolia (public search key, client-exposed by design), and each product
record carries `storesPriceAndInventory` — a per-store array with a NUMERIC unit count
(`purchaseAvailability`) and per-store prices. So we get the real prize: store-level price
+ inventory, and the day-over-day delta of `purchaseAvailability` per (sku, store) ≈
directional UNITS SOLD. No scraping, no Bright Data — the same call the site's search makes.

connId: `binnys`. Snapshot is keyed by `sku|storeCode`; the run's headline delta is
`units_moved` (net depletion since the last pull).

Algolia app id / index / key are env-overridable (`BINNYS_ALGOLIA_*`; defaults discovered
on binnys.com). Self-reports `degraded` if the key rotates or the per-store schema changes.
"""
import argparse, hashlib, json, os, sys, time, urllib.request

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
    pages = min(first.get("nbPages", 1), 100)   # was 40 — don't truncate the full Algolia catalog
    for p in range(1, pages):
        try:
            out += query(p, 1000).get("hits", [])
        except Exception as e:
            log(f"page {p}: {e}"); break
        log(f"page {p + 1}/{pages}: {len(out)} records")
    return out


def _store_price(sp):
    pr = sp.get("prices") or {}
    sale = pr.get("salePrice")
    if sp.get("isOnSale") and sale not in (None, 0):
        return float(sale)
    reg = pr.get("regularPrice")
    try: return float(reg) if reg is not None else None
    except (ValueError, TypeError): return None


def to_snapshot(records):
    """Per-store cells keyed `sku|storeCode` -> {price, qty, name, store}."""
    snap, parsed_qty = {}, 0
    for h in records:
        sku = str(h.get("objectID") or "")
        if not sku:
            continue
        name = h.get("productName")
        for sp in (h.get("storesPriceAndInventory") or []):
            store = str(sp.get("storeCode") or "")
            if not store:
                continue
            qty = sp.get("purchaseAvailability")
            qty = int(qty) if isinstance(qty, (int, float)) else None
            if qty is not None:
                parsed_qty += 1
            snap[f"{sku}|{store}"] = {"price": _store_price(sp), "qty": qty,
                                      "name": name, "store": store, "sku": sku}
    return snap, parsed_qty


def diff_store(prev, cur):
    """Per-(sku,store) movement. units_moved = net depletion (the directional sales proxy)."""
    pk, ck = set(prev), set(cur)
    units_moved = restocked = price_moves = went_oos = 0
    for k in ck & pk:
        p, c = prev[k], cur[k]
        if isinstance(p.get("qty"), int) and isinstance(c.get("qty"), int):
            d = c["qty"] - p["qty"]
            if d < 0: units_moved += -d
            elif d > 0: restocked += d
            if p["qty"] > 0 and c["qty"] == 0: went_oos += 1
        if p.get("price") is not None and c.get("price") is not None and p["price"] != c["price"]:
            price_moves += 1
    return {"cells": len(cur), "had_prev": bool(prev),
            "units_moved": units_moved, "restocked": restocked,
            "price_moves": price_moves, "went_oos": went_oos,
            "new": len(ck - pk), "dropped": len(pk - ck),
            "changed": units_moved}   # headline delta = units depleted since last run


def run_record(movement, n_products, status, warnings):
    rid = "R-BIN" + hashlib.sha1(str(movement.get("cells", "")).encode()).hexdigest()[:3].upper()
    return {"id": rid, "connId": "binnys", "startedAt": 0, "finishedAt": 0, "durationMs": 0,
            "status": status, "trigger": "manual", "total": n_products,
            "degraded": status == "degraded", "warnings": warnings, "healed": [],
            "extracts": [{"id": "binnys_store_cells", "rows": movement["cells"],
                          "delta": movement["units_moved"], "status": status}],
            "movement": movement}


def pull(sample=300, crawl_all=False, limit=None, out=".", state_dir=None, log=print):
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    try:
        records = fetch_records(sample=sample, crawl_all=crawl_all, log=log)
    except Exception as e:
        run = run_record(diff_store({}, {}), 0, "failed",
                         [f"Algolia query failed: {str(e)[:120]} — the public search key may have "
                          "rotated; re-discover it from a binnys.com product page."])
        return {}, [run], run["movement"]
    if limit:
        records = records[:limit]
    cur, parsed_qty = to_snapshot(records)
    if not cur:
        run = run_record(diff_store({}, {}), 0, "failed",
                         ["Algolia returned no per-store inventory (storesPriceAndInventory schema changed)."])
        return {}, [run], run["movement"]

    prev = {}
    snap_path = os.path.join(state_dir, SNAP)
    if os.path.exists(snap_path):
        try: prev = json.load(open(snap_path)).get("cells", {})
        except Exception: prev = {}
    movement = diff_store(prev, cur)

    warnings = []
    if parsed_qty / len(cur) < 0.5:
        warnings.append(f"Numeric per-store qty present on only {parsed_qty}/{len(cur)} cells — "
                        "Binny's storesPriceAndInventory schema may have changed.")
    status = "degraded" if warnings else "success"

    json.dump({"__ts__": int(time.time() * 1000), "cells": cur}, open(snap_path, "w"), indent=2)
    # browsable rollup: one row per (product, store) with price + on-hand units
    header = ["SKU", "Product", "Store", "Price", "Units on hand"]
    rows = [[v["sku"], v["name"], v["store"], v["price"], v["qty"]]
            for k, v in sorted(cur.items())]
    n_products = len({v["sku"] for v in cur.values()})
    datasets = {"binnys_store_cells": {"header": header, "rows": rows[:800],
                                       "total": len(rows), "products": n_products, "movement": movement}}
    json.dump(datasets, open(os.path.join(out, "datasets.json"), "w"), indent=2)
    datasets["binnys_store_cells"]["_rows_full"] = rows   # full set for export (in-memory return only)
    run = run_record(movement, n_products, status, warnings)
    log(f"done: {n_products} products × stores = {len(cur)} cells; "
        + (f"{movement['units_moved']} units moved, {movement['price_moves']} price changes since last run"
           if prev else "baseline (no prior snapshot)"))
    return datasets, [run], movement


def main(argv=None):
    ap = argparse.ArgumentParser(description="Store-level price/inventory tracker for Binny's (Algolia feed).")
    ap.add_argument("--sample", type=int, default=300, help="products to pull (each expands to per-store cells)")
    ap.add_argument("--all", action="store_true", help="paginate the whole index (~31k products)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="./binnys_out")
    a = ap.parse_args(argv)
    _, runs, _ = pull(sample=a.sample, crawl_all=a.all, limit=a.limit, out=a.out, state_dir=a.out)
    print(json.dumps(runs[0], indent=2))
    return 0 if runs[0]["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
