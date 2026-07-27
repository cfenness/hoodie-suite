#!/usr/bin/env python3
"""vtinfo_bbs.py — VIP **Brand Builder** distributor catalog (products.vtinfo.com/bbs).

WHY THIS IS A PLATFORM RECIPE (not a one-off scrape):
  Vermont Information Processing (VIP) hosts a "Brand Builder" product-portfolio app for a huge
  share of US beverage-alcohol DISTRIBUTORS. Every distributor's portfolio lives at the same
  place, keyed only by that distributor's VIP **sourceCode** (a short id like `01191`):

      https://products.vtinfo.com/brandbuilder/<sourceCode>/brands/tab/1   (the Angular UI)
      https://products.vtinfo.com/bbs/v1/distributor/<sourceCode>/{info,brands,products,styles}

  The UI is a single-page app, but it reads a plain, **open JSON API** (the `bbs/v1` service):
  no auth, no cookie, no token — `access-control-allow-origin: *`. So proving ONE distributor
  proves the platform: point this at any sourceCode and you get that distributor's whole book —
  every brand, product, package, and **retail UPC** — as a deterministic pull. That's the
  "prove the system once → every store on it is free" recipe payoff ([[system-scrape-recipes]]),
  and it's the fastest path toward catalog coverage of the major distributors (Columbia, Reyes,
  Breakthru, RNDC, …) one sourceCode at a time.

THE THREE ENDPOINTS WE READ (all GET, all `{code, data, success}` envelopes):
  • /info     → distributor identity (name, vipSourceId, vipCustomerId, logo).
  • /brands   → brand groups → brands[] (name, description, website, socials). We use the group
                names (Domestic/Craft/Import Beer, Cider, Spirits, Wineries, …) to tag each item's
                CATEGORY via brand_name == product_supplier_name (~98% hit on Columbia).
  • /products → the master: products[] each with abv/ibu/style/supplier/ratebeer + product_packages[],
                and each package carries `dist_item_code` (the distributor's item #) + `retail_upc`.

GRAIN: one row per **package** (product × pack size) — the master-item grain ([[master-item-grain]]),
  denormalized with product + distributor context. Identity = distributor_id | dist_item_code
  (always present on Columbia: 6756/6756). UPCs are zero-padded to 12 the way the app itself does.

SELF-HEALING: if /products lands products but yields 0 package rows, or retail_upc fill collapses,
  the run is marked `degraded` with warnings[] (the package selectors drifted) rather than emitting
  bad data — same honesty contract as the other owned scrapers.

CLI:  python vtinfo_bbs.py --dist 01191
      python vtinfo_bbs.py --discover https://a-distributor.com/brands   # find its sourceCode
"""
import argparse, hashlib, json, os, re, time, urllib.request, urllib.parse, urllib.error

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
BBS = "https://products.vtinfo.com/bbs/v1/distributor"
TABLE = "vip_brandbuilder_items"
# Snapshots are runtime state → default under the git-ignored agent_state/ (not the repo root).
_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_state", "vip_brandbuilder")

# Known distributor sourceCodes, harvested from each distributor's public Brand Builder page
# (the `brandbuilder/<code>/` path, or an embedded products.vtinfo.com iframe — see discover()).
# Grow this map; the daily pass runs whatever is enabled here.
DISTRIBUTORS = {
    "01191": "Columbia Distributing - WA",
}

# Stable column set → stable Parquet schema across pulls (warehouse projects onto exactly these).
FIELDS = ["distributor_id", "distributor_name", "vip_source_id", "vip_customer_id",
          "category", "brand", "product_id", "product_name", "product_type",
          "style_name", "style_type", "sub_style_type", "abv", "ibu",
          "ratebeer_score", "ratebeer_style", "dist_item_code", "retail_upc",
          "retail_upc_raw", "package_name", "beverage_id", "image", "sell_sheet",
          "description", "pulled_at"]


def _get(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def discover(url, log=print):
    """Read a distributor's public page and pull the Brand Builder sourceCode from either a
    products.vtinfo.com/brandbuilder/<code> link or an embedded iframe. Returns the code or None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:
        log("discover: fetch failed: %s" % str(e)[:120]); return None
    m = re.search(r'products\.vtinfo\.com/(?:brandbuilder|bbs/v1/distributor)/(\w+)', html)
    if m:
        log("discover: sourceCode=%s" % m.group(1)); return m.group(1)
    log("discover: no Brand Builder sourceCode on %s" % url); return None


def _brand_categories(brands_data):
    """brand_name → category, read from the non-'All' brand groups (Spirits, Craft Beer, …)."""
    cat = {}
    for g in (brands_data or []):
        if (g.get("group_name") or "") == "All":
            continue
        for b in (g.get("brands") or []):
            nm = b.get("brand_name")
            if nm and nm not in cat:
                cat[nm] = g.get("group_name")
    return cat


def _norm_upc(u):
    """Zero-pad to a 12-digit UPC-A when short — exactly what the Brand Builder UI does
    (`retail_upc.padStart(12,'0')` for length <= 12). Longer codes pass through untouched."""
    u = (str(u or "")).strip()
    return u.zfill(12) if (u.isdigit() and len(u) <= 12) else u


def _rows_for(code, info, products, cat, pulled_at):
    dname = info.get("distributor_name") or DISTRIBUTORS.get(code, "")
    rows = []
    for p in (products or []):
        brand = p.get("product_supplier_name") or ""
        base = {
            "distributor_id": code, "distributor_name": dname,
            "vip_source_id": info.get("vipSourceId"), "vip_customer_id": info.get("vipCustomerId"),
            "category": cat.get(brand, ""), "brand": brand,
            "product_id": p.get("product_id"), "product_name": p.get("product_name"),
            "product_type": p.get("product_type"), "style_name": p.get("product_style_name"),
            "style_type": p.get("product_style_type"), "sub_style_type": p.get("product_sub_style_type"),
            "abv": p.get("product_abv"), "ibu": p.get("product_ibu"),
            "ratebeer_score": p.get("product_ratebeer_ovrl"), "ratebeer_style": p.get("product_ratebeer_style"),
            "image": p.get("product_image_small") or p.get("product_image_large"),
            "sell_sheet": p.get("product_sell_sheet"),
            "description": (p.get("product_description") or "")[:600],
        }
        for pk in (p.get("product_packages") or []):
            raw = pk.get("retail_upc")
            rows.append({**base,
                         "dist_item_code": pk.get("dist_item_code"),
                         "retail_upc": _norm_upc(raw), "retail_upc_raw": raw,
                         "package_name": pk.get("package_name"),
                         "beverage_id": pk.get("beverage_id"), "pulled_at": pulled_at})
    return rows


def _snap_diff(rows, code, state_dir):
    """Snapshot per distributor (keyed dist_item_code) → count new / dropped since last pull."""
    cur = {r.get("dist_item_code") or ("%s|%s" % (r["product_id"], r["retail_upc"])): 1 for r in rows}
    sp = os.path.join(state_dir, "vtinfo_bbs_%s_snapshot.json" % code)
    prev = {}
    if os.path.exists(sp):
        try: prev = json.load(open(sp)).get("cells", {})
        except Exception: prev = {}
    new = sum(1 for k in cur if k not in prev)
    dropped = sum(1 for k in prev if k not in cur)
    try:
        json.dump({"__ts__": int(time.time() * 1000), "cells": cur}, open(sp, "w"))
    except Exception:
        pass
    return new, dropped


def _run(code, n, new, status, warnings, started, dropped=0):
    return {"id": "R-BB" + hashlib.sha1((code + str(n)).encode()).hexdigest()[:3].upper(),
            "connId": "vip-brandbuilder", "startedAt": started, "finishedAt": int(time.time() * 1000),
            "durationMs": 0, "status": status, "trigger": "manual", "total": n,
            "degraded": status != "success", "warnings": warnings, "healed": [],
            "extracts": [{"id": TABLE, "rows": n, "delta": new, "dropped": dropped, "status": status}]}


def pull(dist=None, dists=None, land=True, out=None, state_dir=None, log=print):
    """Pull one or more distributors' Brand Builder catalogs → rows, land to the warehouse.
    `dist`/`dists` = sourceCode(s); default = every entry in DISTRIBUTORS."""
    state_dir = state_dir or out or _STATE
    os.makedirs(state_dir, exist_ok=True)
    started = int(time.time() * 1000)
    pulled_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    codes = ([dist] if dist else None) or dists or list(DISTRIBUTORS.keys())

    all_rows, runs, tot_new, tot_drop = [], [], 0, 0
    for code in codes:
        try:
            info = (_get("%s/%s/info" % (BBS, code)).get("data")) or {}
            brands = (_get("%s/%s/brands" % (BBS, code)).get("data")) or []
            products = (_get("%s/%s/products" % (BBS, code)).get("data")) or []
        except Exception as e:
            runs.append(_run(code, 0, 0, "failed", ["fetch failed: %s" % str(e)[:140]], started))
            log("vip-brandbuilder/%s: fetch failed: %s" % (code, str(e)[:120]))
            continue

        rows = _rows_for(code, info, products, _brand_categories(brands), pulled_at)
        warns = []
        if products and not rows:
            warns.append("%d products but 0 package rows — product_packages selector drift" % len(products))
        if rows:
            fill = sum(1 for r in rows if r["retail_upc"]) / len(rows)
            if fill < 0.5:
                warns.append("retail_upc fill %d%% (<50%%) — package schema drift" % round(fill * 100))
        new, dropped = _snap_diff(rows, code, state_dir)
        tot_new += new; tot_drop += dropped
        all_rows += rows
        status = "degraded" if warns else "success"
        runs.append(_run(code, len(rows), new, status, warns, started, dropped))
        log("vip-brandbuilder/%s (%s): %d items, %d brands, %d products (%d new, %d dropped) — %s"
            % (code, info.get("distributor_name") or code, len(rows),
               sum(len(g.get("brands") or []) for g in brands if g.get("group_name") != "All"),
               len(products), new, dropped, status))

    if land and all_rows:
        try:
            import warehouse
            warehouse.write_accumulate(
                TABLE, all_rows,
                key=lambda r: "%s|%s" % (r["distributor_id"], r.get("dist_item_code")
                                         or ("%s|%s" % (r["product_id"], r["retail_upc"]))),
                fields=FIELDS)
            log("landed %s: +%d rows (accumulated)" % (TABLE, len(all_rows)))
        except Exception as e:
            log("%s land skipped: %s" % (TABLE, str(e)[:100]))

    header = ["Distributor", "Category", "Brand", "Product", "Type", "ABV", "Item#", "UPC", "Package"]
    prev = [[r["distributor_name"], r["category"], r["brand"], r["product_name"], r["product_type"],
             r["abv"], r["dist_item_code"], r["retail_upc"], r["package_name"]] for r in all_rows]
    ds = {TABLE: {"header": header, "rows": prev[:1000], "total": len(all_rows), "_rows_full": prev}}
    return ds, runs, {"sampled": len(all_rows), "changed": tot_new, "dropped": tot_drop}


def main(argv=None):
    ap = argparse.ArgumentParser(description="VIP Brand Builder distributor catalog (products.vtinfo.com/bbs).")
    ap.add_argument("--dist", help="one distributor sourceCode (e.g. 01191); default = all in DISTRIBUTORS")
    ap.add_argument("--discover", help="a distributor URL → print its Brand Builder sourceCode and exit")
    ap.add_argument("--no-land", action="store_true", help="parse only, don't write to the warehouse")
    ap.add_argument("--out", default=None, help="snapshot dir (default: agent_state/vip_brandbuilder)")
    a = ap.parse_args(argv)
    if a.discover:
        print(discover(a.discover)); return
    ds, runs, mv = pull(dist=a.dist, land=not a.no_land, out=a.out, state_dir=a.out)
    r = list(ds.values())[0]
    for row in r["rows"][:15]:
        print("  •", str(row[2])[:22], "|", str(row[3])[:34], "|", row[5], "| item", row[6], "| upc", row[7])
    print("runs:", [(x["extracts"][0]["id"], x["status"], x["total"], x["extracts"][0]["delta"]) for x in runs])
    print("movement:", mv)


if __name__ == "__main__":
    main()
