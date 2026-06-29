#!/usr/bin/env python3
"""instacart_scraper.py — store-level product/price via Bright Data's managed Instacart dataset.

Instacart explicitly forbids scraping and is DataDome-protected — the Web Unlocker 403s it.
The sanctioned route is Bright Data's **managed Instacart dataset** (Web Scraper API), which
takes Instacart store/category URLs and returns structured per-store product records. The
vendor handles the protection + compliance; it's paid and ToS-gray (your informed call).

connId: `instacart`. NEEDS, from your Bright Data dashboard / account:
    export BRIGHTDATA_API_KEY=...                  # REST key
    export BRIGHTDATA_INSTACART_DATASET=gd_...      # the Instacart products dataset id
    # FULL store (all departments) — point at the store root / department collections, NOT one aisle:
    export BRIGHTDATA_INSTACART_URLS="https://www.instacart.com/store/<retailer>,..."
    export BRIGHTDATA_INSTACART_INPUT_EXTRA='{"num_of_products": 5000}'   # optional depth (dataset-specific)

Because the dataset's exact field names depend on which BD Instacart product you pick, this
self-reports `degraded` and DUMPS the first batch of raw records (instacart_debug.json) on
the first live run so we can lock the field mapping — then we pin price/availability/store.
Store-level: each record is a product at a specific Instacart retailer location.
"""
import argparse, hashlib, json, os, sys, time
import brightdata
from abc_fws_scraper import diff_snapshots

DATASET = os.environ.get("BRIGHTDATA_INSTACART_DATASET", "")
URLS    = [u.strip() for u in os.environ.get("BRIGHTDATA_INSTACART_URLS", "").split(",") if u.strip()]
SNAP    = "instacart_snapshot.json"

# FULL STORE, not just the alcohol aisle: an Instacart store is a whole grocery catalog
# (produce, pantry, dairy, … plus alcohol). Point BRIGHTDATA_INSTACART_URLS at the STORE
# root(s) / department collections to cover everything — not a single beverage collection.
# Depth/discovery knobs vary by BD Instacart dataset, so they're passed through verbatim via
# BRIGHTDATA_INSTACART_INPUT_EXTRA (a JSON object merged into every input record), e.g.
#   export BRIGHTDATA_INSTACART_INPUT_EXTRA='{"num_of_products": 5000}'
def _input_extra():
    raw = os.environ.get("BRIGHTDATA_INSTACART_INPUT_EXTRA", "").strip()
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}

# candidate field names (BD Instacart schemas vary by product) — first match wins
F_NAME  = ("name", "title", "product_name", "productName")
F_PRICE = ("price", "current_price", "sale_price", "unit_price")
F_STORE = ("store", "retailer", "store_name", "warehouse", "location")
F_ID    = ("id", "product_id", "sku", "item_id", "upc", "url")
F_AVAIL = ("availability", "in_stock", "available", "stock_status")


def _first(rec, keys):
    for k in keys:
        if isinstance(rec, dict) and rec.get(k) not in (None, ""):
            return rec[k]
    return None


def to_snapshot(records):
    snap, priced = {}, 0
    for r in records:
        if not isinstance(r, dict):
            continue
        pid = str(_first(r, F_ID) or "")
        store = str(_first(r, F_STORE) or "")
        if not pid:
            continue
        p = _first(r, F_PRICE)
        try: price = float(str(p).replace("$", "").replace(",", "")) if p is not None else None
        except (ValueError, TypeError): price = None
        if price is not None:
            priced += 1
        av = _first(r, F_AVAIL)
        instock = None
        if isinstance(av, bool): instock = av
        elif isinstance(av, str): instock = "in" in av.lower() and "out" not in av.lower()
        snap[f"{store}|{pid}"] = {"price": price, "instock": instock,
                                  "name": _first(r, F_NAME), "store": store}
    return snap, priced


def run_record(movement, n, status, warnings):
    rid = "R-IC" + hashlib.sha1(str(movement.get("sampled", "")).encode()).hexdigest()[:3].upper()
    return {"id": rid, "connId": "instacart", "startedAt": 0, "finishedAt": 0, "durationMs": 0,
            "status": status, "trigger": "manual", "total": n,
            "degraded": status == "degraded", "warnings": warnings, "healed": [],
            "extracts": [{"id": "instacart_products", "rows": movement["sampled"],
                          "delta": movement["changed"], "status": status}],
            "movement": movement}


def pull(sample=None, crawl_all=False, limit=None, out=".", state_dir=None, log=print, urls=None):
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    targets = urls or URLS
    if not (os.environ.get("BRIGHTDATA_API_KEY") and DATASET and targets):
        run = run_record(diff_snapshots({}, {}), 0, "failed",
                         ["Instacart needs BRIGHTDATA_API_KEY + BRIGHTDATA_INSTACART_DATASET (dataset id "
                          "from the BD dashboard) + BRIGHTDATA_INSTACART_URLS. For the FULL store (all "
                          "departments, not just alcohol) point the URLs at the store root / department "
                          "collections; tune depth with BRIGHTDATA_INSTACART_INPUT_EXTRA (JSON)."])
        return {}, [run], run["movement"]
    try:
        extra = _input_extra()                       # full-store depth/discovery knobs (dataset-specific)
        records = brightdata.dataset_run(DATASET, [dict({"url": u}, **extra) for u in targets])
    except Exception as e:
        run = run_record(diff_snapshots({}, {}), 0, "failed", [f"Instacart dataset run failed: {str(e)[:140]}"])
        return {}, [run], run["movement"]

    # first-run: dump raw records so we can confirm the field mapping for this BD dataset
    json.dump(records[:50] if isinstance(records, list) else records,
              open(os.path.join(out, "instacart_debug.json"), "w"), indent=2, default=str)
    cur, priced = to_snapshot(records if isinstance(records, list) else [])
    if not cur:
        run = run_record(diff_snapshots({}, {}), 0, "degraded",
                         ["Dataset returned records but none mapped to product/store — see "
                          "instacart_debug.json and pin the field names."])
        return {}, [run], run["movement"]

    prev = {}
    snap_path = os.path.join(state_dir, SNAP)
    if os.path.exists(snap_path):
        try: prev = json.load(open(snap_path)).get("cells", {})
        except Exception: prev = {}
    movement = diff_snapshots(prev, cur)

    warnings = []
    if priced / len(cur) < 0.5:
        warnings.append(f"Price mapped on only {priced}/{len(cur)} records — confirm field names "
                        "in instacart_debug.json (BD Instacart schema varies by product).")
    status = "degraded" if warnings else "success"

    json.dump({"__ts__": int(time.time() * 1000), "cells": cur}, open(snap_path, "w"), indent=2)
    header = ["Store", "Product", "Price", "In Stock"]
    rows = [[v["store"], v["name"], v["price"], v["instock"]] for k, v in sorted(cur.items())]
    datasets = {"instacart_products": {"header": header, "rows": rows[:800], "total": len(rows),
                                       "stores": len({v["store"] for v in cur.values()}), "movement": movement}}
    json.dump(datasets, open(os.path.join(out, "datasets.json"), "w"), indent=2)
    run = run_record(movement, len(cur), status, warnings)
    log(f"done: {len(cur)} product-cells across {datasets['instacart_products']['stores']} stores; "
        + (f"{movement['changed']} moved since last run" if prev else "baseline"))
    return datasets, [run], movement


def main(argv=None):
    ap = argparse.ArgumentParser(description="Store-level Instacart product/price via Bright Data managed dataset.")
    ap.add_argument("--urls", help="comma-separated Instacart store/category URLs (else BRIGHTDATA_INSTACART_URLS)")
    ap.add_argument("--out", default="./instacart_out")
    a = ap.parse_args(argv)
    u = [x.strip() for x in a.urls.split(",")] if a.urls else None
    _, runs, _ = pull(out=a.out, state_dir=a.out, urls=u)
    print(json.dumps(runs[0], indent=2))
    return 0 if runs[0]["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
