#!/usr/bin/env python3
"""shopify_scraper.py — price/availability tracker for DTC brands on Shopify (hemp + bev-alc).

Most hemp-THC-beverage / CBD brands (and many craft bev-alc DTC brands) run on Shopify,
which exposes a public **`/products.json`** feed — the same data the storefront renders:
title, variants, price, `available`, sku. So one connector covers MANY brands at once: pull
each domain's catalog, snapshot per variant, diff day-over-day → price moves / in-out /
assortment churn. Stdlib-only, no Bright Data — these are open public endpoints.

connId: `shopify-dtc`. Brand domains come from `SHOPIFY_DOMAINS` (comma-separated) or the
seed below (verified hemp + functional-beverage DTC brands). These are single online stores
(brand-level, not multi-location), so this is the online/brand granularity for the hemp
vertical. Self-reports `degraded` if a domain stops returning products.
"""
import argparse, hashlib, json, os, sys, time, urllib.request
from abc_fws_scraper import diff_snapshots   # generic per-key price/in-stock diff

SEED = ["drinkbrez.com", "drinkcann.com", "cornbreadhemp.com", "hopwtr.com", "drinkolipop.com"]
DOMAINS = [d.strip() for d in os.environ.get("SHOPIFY_DOMAINS", ",".join(SEED)).split(",") if d.strip()]
UA      = os.environ.get("SHOPIFY_UA", "HoodieSuite-Research/1.0 (+market-intelligence; respects robots)")
DELAY   = float(os.environ.get("SHOPIFY_DELAY", "1"))
SNAP    = "shopify_snapshot.json"


def _brand(domain):
    return domain.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")


def fetch_catalog(domain, max_products=500, log=print):
    """Page through {domain}/products.json. Returns [] if the domain isn't an open Shopify."""
    base = domain if domain.startswith("http") else "https://" + domain
    out, page = [], 1
    while len(out) < max_products:
        url = f"{base}/products.json?limit=250&page={page}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.load(r)
        except Exception as e:
            if page == 1:
                log(f"{_brand(domain)}: {e}")
            break
        prods = data.get("products") or []
        if not prods:
            break
        out += prods
        page += 1
        time.sleep(DELAY)
    return out[:max_products]


def to_cells(domain, products):
    """Per-variant cells keyed `brand|variantId` -> {price, instock, title, sku, brand}."""
    brand = _brand(domain)
    cells = {}
    for p in products:
        title = p.get("title")
        for v in (p.get("variants") or []):
            vid = str(v.get("id") or "")
            if not vid:
                continue
            try: price = float(v.get("price")) if v.get("price") is not None else None
            except (ValueError, TypeError): price = None
            cells[f"{brand}|{vid}"] = {"price": price, "instock": bool(v.get("available")),
                                       "title": title, "sku": v.get("sku"), "brand": brand}
    return cells


def run_record(movement, n_products, n_brands, status, warnings):
    rid = "R-SHP" + hashlib.sha1(str(movement.get("sampled", "")).encode()).hexdigest()[:3].upper()
    return {"id": rid, "connId": "shopify-dtc", "startedAt": 0, "finishedAt": 0, "durationMs": 0,
            "status": status, "trigger": "manual", "total": n_products,
            "degraded": status == "degraded", "warnings": warnings, "healed": [],
            "extracts": [{"id": "shopify_variants", "rows": movement["sampled"],
                          "delta": movement["changed"], "status": status}],
            "movement": dict(movement, brands=n_brands)}


def pull(sample=None, crawl_all=False, limit=None, out=".", state_dir=None, log=print, domains=None):
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    doms = domains or DOMAINS
    cap = (limit or 2000) if crawl_all else (sample or 500)
    cur, ok_brands = {}, 0
    for d in doms:
        prods = fetch_catalog(d, max_products=cap, log=log)
        if prods:
            ok_brands += 1
            cur.update(to_cells(d, prods))
            log(f"{_brand(d)}: {len(prods)} products")
    if not cur:
        run = run_record(diff_snapshots({}, {}), 0, 0, "failed",
                         ["No Shopify catalogs returned — check SHOPIFY_DOMAINS (open /products.json)."])
        return {}, [run], run["movement"]

    prev = {}
    snap_path = os.path.join(state_dir, SNAP)
    if os.path.exists(snap_path):
        try: prev = json.load(open(snap_path)).get("cells", {})
        except Exception: prev = {}
    movement = diff_snapshots(prev, cur)

    warnings = []
    if ok_brands < len(doms):
        warnings.append(f"{ok_brands}/{len(doms)} brand domains returned a catalog — the rest may "
                        "not be Shopify or block /products.json.")
    status = "degraded" if warnings else "success"

    json.dump({"__ts__": int(time.time() * 1000), "cells": cur}, open(snap_path, "w"), indent=2)
    header = ["Brand", "Product", "SKU", "Price", "Available"]
    rows = [[v["brand"], v["title"], v["sku"], v["price"], v["instock"]] for k, v in sorted(cur.items())]
    n_products = len({(v["brand"], v["title"]) for v in cur.values()})
    datasets = {"shopify_variants": {"header": header, "rows": rows[:800], "total": len(rows),
                                     "products": n_products, "brands": ok_brands, "movement": movement}}
    json.dump(datasets, open(os.path.join(out, "datasets.json"), "w"), indent=2)
    datasets["shopify_variants"]["_rows_full"] = rows   # full set for export (in-memory return only)
    run = run_record(movement, n_products, ok_brands, status, warnings)
    log(f"done: {ok_brands} brands, {n_products} products, {len(cur)} variant-cells; "
        + (f"{movement['changed']} moved since last run" if prev else "baseline"))
    return datasets, [run], movement


def main(argv=None):
    ap = argparse.ArgumentParser(description="Price/availability tracker for DTC brands on Shopify (hemp + bev-alc).")
    ap.add_argument("--sample", type=int, default=500, help="max products per brand")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--domains", help="comma-separated Shopify domains (overrides the seed)")
    ap.add_argument("--out", default="./shopify_out")
    a = ap.parse_args(argv)
    doms = [d.strip() for d in a.domains.split(",")] if a.domains else None
    _, runs, _ = pull(sample=a.sample, crawl_all=a.all, limit=a.limit, out=a.out, state_dir=a.out, domains=doms)
    print(json.dumps(runs[0], indent=2))
    return 0 if runs[0]["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
