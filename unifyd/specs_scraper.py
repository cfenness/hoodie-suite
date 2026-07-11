#!/usr/bin/env python3
"""specs_scraper.py — STORE-LEVEL price + availability tracker for Spec's (specsonline.com).

Spec's serves bots (200) and embeds a per-store `variants` object right in the product
page — 114 stores, each with `inStock` (bool) and `unitPrice` (cents) keyed by a store code
in `code` ("<storeCode>-<sku>"). So we fetch the product page directly (no Bright Data) and
read per-store price + availability. Snapshot is keyed `sku|storeCode`; day-over-day the
per-store in/out and price moves are the directional signal (Spec's exposes binary in/out,
not a unit count like Binny's).

connId: `specs`. Harvest product URLs from the sitemap (direct), poll a deterministic
sample, diff vs the prior snapshot. Self-reports `degraded` if the `variants` block can't
be parsed on most pages (markup drift).
"""
import argparse, hashlib, json, os, re, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polite
from abc_fws_scraper import diff_snapshots   # generic per-key price/in-stock diff
SPECS_MIN_INT = float(os.environ.get("SPECS_MIN_INTERVAL", "0.6"))
SPECS_PROXY   = os.environ.get("SPECS_PROXY", "0") == "1"

BASE      = "https://specsonline.com"
SITEMAP   = BASE + "/sitemap.xml"
DELAY     = float(os.environ.get("SPECS_DELAY", "1"))
SNAP      = "specs_snapshot.json"
UA        = os.environ.get("SPECS_UA", "HoodieSuite-Research/1.0 (+market-intelligence; respects robots)")
LOC_RE    = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.I)
LD_RE     = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def _http(url, timeout=25):
    # via polite: per-host rate-limit + backoff + circuit breaker + optional BD residential proxy (Tier 2)
    body = polite.get(url, min_interval=SPECS_MIN_INT, jitter=SPECS_MIN_INT, timeout=timeout,
                      headers={"Accept-Encoding": "identity"}, breaker_after=4,
                      use_proxy=SPECS_PROXY, host="specsonline.com")
    return body.decode("utf-8", "replace")


def harvest_ids(max_products=20000, log=print):
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
                    return out
        log(f"{cs.rsplit('/',1)[-1]}: {len(out)} products so far")
    return out


def pick_sample(catalog, n):
    if n <= 0 or n >= len(catalog):
        return list(catalog)
    cat = sorted(catalog, key=lambda t: t[0])
    stride = len(cat) / float(n)
    return [cat[int(i * stride)] for i in range(n)]


def _maybe_unescape(s):
    try: return s.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
    except Exception: return s


def _variants_obj(html):
    """The page embeds an (escaped) JSON state with a "variants":{...} object keyed by
    store. Pull it whether the page is escaped or not, via brace-balancing."""
    for s in (html, _maybe_unescape(html)):
        i = s.find('"variants"')
        if i < 0:
            continue
        j = s.find("{", i)
        if j < 0:
            continue
        depth = 0
        for k in range(j, len(s)):
            ch = s[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[j:k + 1])
                    except Exception:
                        break
    return None


def _name(html):
    for blk in LD_RE.findall(html):
        try: data = json.loads(blk)
        except Exception: continue
        for d in (data if isinstance(data, list) else [data]):
            if isinstance(d, dict) and d.get("@type") == "Product" and d.get("name"):
                return d["name"]
    return None


def parse_stores(html):
    """Return per-store rows [{store, sku, instock, price}] from the variants block."""
    v = _variants_obj(html)
    if not isinstance(v, dict):
        return [], None
    name = _name(html)
    rows = []
    for d in v.values():
        if not isinstance(d, dict):
            continue
        code = str(d.get("code") or "")
        store, _, sku = code.partition("-")
        if not store:
            continue
        up = d.get("unitPrice")
        price = (up / 100.0) if isinstance(up, (int, float)) else None
        rows.append({"store": store, "sku": sku or "", "instock": bool(d.get("inStock")),
                     "price": price})
    return rows, name


def run_record(movement, n_products, status, warnings):
    rid = "R-SPX" + hashlib.sha1(str(movement.get("sampled", "")).encode()).hexdigest()[:3].upper()
    return {"id": rid, "connId": "specs", "startedAt": 0, "finishedAt": 0, "durationMs": 0,
            "status": status, "trigger": "manual", "total": n_products,
            "degraded": status == "degraded", "warnings": warnings, "healed": [],
            "extracts": [{"id": "specs_store_cells", "rows": movement["sampled"],
                          "delta": movement["changed"], "status": status}],
            "movement": movement}


def pull(sample=30, crawl_all=False, limit=None, out=".", state_dir=None, log=print):
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    catalog = harvest_ids(log=log)
    if not catalog:
        run = run_record(diff_snapshots({}, {}), 0, "failed", ["No products found in sitemap."])
        return {}, [run], run["movement"]
    targets = (catalog if limit is None else catalog[:limit]) if crawl_all else pick_sample(catalog, sample)
    log(f"catalog {len(catalog)} products; fetching {len(targets)} (per-store variants)")

    cur, ok_n, names = {}, 0, {}
    # FAST: concurrent per-page fetch (plain HTTP) instead of serial DELAY.
    import threading
    from concurrent.futures import ThreadPoolExecutor
    workers = int(os.environ.get("SPECS_WORKERS", "12"))
    jitter = float(os.environ.get("SPECS_JITTER", "0.2"))
    lock = threading.Lock()
    def _one(t):
        nonlocal ok_n
        slug, url = t
        try:
            rows, name = parse_stores(_http(url))
            with lock:
                if rows:
                    ok_n += 1
                for r in rows:
                    cur[f"{slug}|{r['store']}"] = {"price": r["price"], "instock": r["instock"],
                                                   "store": r["store"], "slug": slug, "sku": r["sku"], "name": name}
                if name:
                    names[slug] = name
        except Exception as e:
            log(f"  {slug}: {e}")
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
    snap_path = os.path.join(state_dir, SNAP)
    if os.path.exists(snap_path):
        try: prev = json.load(open(snap_path)).get("cells", {})
        except Exception: prev = {}
    movement = diff_snapshots(prev, cur)

    n_products = len({v["slug"] for v in cur.values()})
    warnings = []
    if not cur or ok_n / max(1, len(targets)) < 0.5:
        warnings.append(f"Per-store variants parsed on only {ok_n}/{len(targets)} pages — "
                        "the Spec's product-page variants block may have changed.")
    status = "failed" if not cur else ("degraded" if warnings else "success")

    json.dump({"__ts__": int(time.time() * 1000), "cells": cur}, open(snap_path, "w"), indent=2)
    header = ["SKU", "Product", "Store", "Price", "In Stock"]
    rows = [[v["sku"] or v["slug"], v["name"], v["store"], v["price"], v["instock"]]
            for k, v in sorted(cur.items())]
    datasets = {"specs_store_cells": {"header": header, "rows": rows[:800],
                                      "total": len(rows), "products": n_products, "movement": movement}}
    json.dump(datasets, open(os.path.join(out, "datasets.json"), "w"), indent=2)
    datasets["specs_store_cells"]["_rows_full"] = rows   # full set for export (in-memory return only)
    run = run_record(movement, n_products, status, warnings)
    log(f"done: {n_products} products × stores = {len(cur)} cells; "
        + (f"{movement['changed']} store-cells moved since last run" if prev else "baseline"))
    return datasets, [run], movement


def main(argv=None):
    ap = argparse.ArgumentParser(description="Store-level price/availability tracker for Spec's.")
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="./specs_out")
    a = ap.parse_args(argv)
    _, runs, _ = pull(sample=a.sample, crawl_all=a.all, limit=a.limit, out=a.out, state_dir=a.out)
    print(json.dumps(runs[0], indent=2))
    return 0 if runs[0]["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
