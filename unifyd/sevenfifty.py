#!/usr/bin/env python3
"""sevenfifty.py — SevenFifty / Provi distributor **storefront** catalogs (*.storefronts.site).

WHY THIS IS A PLATFORM RECIPE (not a one-off scrape):
  SevenFifty (now Provi) powers the B2B "marketplace storefront" for a large slice of US
  beverage-alcohol DISTRIBUTORS. Every distributor storefront lives on the same platform,
  keyed only by its subdomain slug — and each one exposes a plain, **open JSON search API**:

      https://<slug>.storefronts.site/search.json?page=<n>&per_page=100
             &sort=score&direction=desc&searched_from=marketplace-storefronts

  No auth, no cookie: the catalog (the distributor's real item numbers / SKUs, producer,
  supplier, style, appellation, size, case pack, image) comes back for anyone. Only partner
  PRICING is gated behind login (the `prices` arrays are empty when unauthenticated) — so this
  is an **item-master** pull, not a price pull. `meta.total_pages` drives pagination.

  So proving ONE storefront proves the platform: point this at any slug and you get that
  distributor's whole book. Johnson Brothers (`johnsonbrothers`) is the seed — 25,590 items.
  This is a direct line to the big distributors' catalogs (Reyes, Breakthru, RNDC, Southern
  Glazer's, …) one slug at a time — the "prove the system once → every store on it is free"
  recipe payoff ([[system-scrape-recipes]]).

GRAIN: one row per **item** (a distributor SKU). Identity = storefront | sku. Snapshot per
  storefront → new / dropped since last pull. If the API reports items (`meta.total` > 0) but we
  extract 0 rows, or SKU fill collapses, the run self-reports `degraded` rather than emitting
  bad data.

CLI:  python sevenfifty.py --store johnsonbrothers
      python sevenfifty.py --store johnsonbrothers --cap 3   # first 3 pages only (smoke)
"""
import argparse, hashlib, json, os, time, urllib.request, urllib.parse, urllib.error

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
TABLE = "sevenfifty_items"
PER_PAGE = 100
# Snapshots are runtime state → default under the git-ignored agent_state/ (not the repo root).
_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_state", "sevenfifty")

# Known distributor storefront slugs (the `<slug>.storefronts.site` subdomain). Grow this map;
# the daily pass runs whatever is enabled here.
STOREFRONTS = {
    "johnsonbrothers": "Johnson Brothers",
}

# Stable column set → stable Parquet schema across pulls.
FIELDS = ["storefront", "distributor", "sku", "sevenfifty_id", "token", "name", "name_full",
          "producer", "supplier", "product_type", "style", "style_line", "subtype",
          "appellation", "country", "region", "subregion", "size", "size_formatted",
          "case_size", "container_type", "raw_materials", "status", "vendor_id",
          "image_url", "thumbnail_url", "display_url", "pulled_at"]


def _get(store, page, timeout=40):
    q = urllib.parse.urlencode({"page": page, "per_page": PER_PAGE, "sort": "score",
                                "direction": "desc", "searched_from": "marketplace-storefronts"})
    url = "https://%s.storefronts.site/search.json?%s" % (store, q)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _row(store, dname, it, pulled_at):
    rm = it.get("raw_material_names")
    return {
        "storefront": store, "distributor": dname,
        "sku": it.get("sku"), "sevenfifty_id": it.get("id"), "token": it.get("token"),
        "name": it.get("name"), "name_full": it.get("name_with_vintage_and_producer"),
        "producer": it.get("producer_name"), "supplier": it.get("supplier_name"),
        "product_type": it.get("product_type_name"), "style": it.get("style_name"),
        "style_line": it.get("style_line"), "subtype": it.get("subtype_name"),
        "appellation": it.get("appellation_name"), "country": it.get("country_name"),
        "region": it.get("region_name"), "subregion": it.get("subregion_name"),
        "size": it.get("size"), "size_formatted": it.get("size_formatted"),
        "case_size": it.get("case_size"), "container_type": it.get("container_type_formatted"),
        "raw_materials": ", ".join(rm) if isinstance(rm, list) else (rm or ""),
        "status": it.get("status"), "vendor_id": it.get("vendor_id"),
        "image_url": it.get("image_url"), "thumbnail_url": it.get("thumbnail_image_url"),
        "display_url": it.get("display_url") or it.get("link"), "pulled_at": pulled_at,
    }


def _snap_diff(rows, store, state_dir):
    cur = {r["sku"]: 1 for r in rows if r.get("sku")}
    sp = os.path.join(state_dir, "sevenfifty_%s_snapshot.json" % store)
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


def _run(store, n, new, status, warnings, started, dropped=0):
    return {"id": "R-SF" + hashlib.sha1((store + str(n)).encode()).hexdigest()[:3].upper(),
            "connId": "sevenfifty", "startedAt": started, "finishedAt": int(time.time() * 1000),
            "durationMs": 0, "status": status, "trigger": "manual", "total": n,
            "degraded": status != "success", "warnings": warnings, "healed": [],
            "extracts": [{"id": TABLE, "rows": n, "delta": new, "dropped": dropped, "status": status}]}


def crawl_store(store, cap=None, delay=0.3, log=print):
    """All items in one storefront, across every result page (or the first `cap` pages).
    Returns (rows, meta_total, warnings)."""
    dname = STOREFRONTS.get(store, store)
    pulled_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    first = _get(store, 1)
    meta = first.get("meta") or {}
    total_pages = int(meta.get("total_pages") or 1)
    total = int(meta.get("total") or 0)
    if cap:
        total_pages = min(total_pages, cap)
    seen, rows, warns = set(), [], []
    for pg in range(1, total_pages + 1):
        data = first if pg == 1 else None
        for attempt in range(3):
            try:
                data = data or _get(store, pg)
                break
            except Exception as e:
                if attempt == 2:
                    warns.append("page %d failed: %s" % (pg, str(e)[:80]))
                    data = {"results": []}
                else:
                    time.sleep(1.0 + attempt)
        for it in (data.get("results") or []):
            sku = it.get("sku")
            k = sku or it.get("id")
            if k in seen:
                continue
            seen.add(k)
            rows.append(_row(store, dname, it, pulled_at))
        if pg % 25 == 0:
            log("  %s: page %d/%d, %d items so far" % (store, pg, total_pages, len(rows)))
        if delay and pg < total_pages:
            time.sleep(delay)
    return rows, total, warns


def pull(store=None, stores=None, cap=None, land=True, out=None, state_dir=None, log=print):
    """Pull one or more distributor storefronts → item rows, land to the warehouse.
    `store`/`stores` = slug(s); default = every entry in STOREFRONTS. `cap` limits pages (smoke)."""
    state_dir = state_dir or out or _STATE
    os.makedirs(state_dir, exist_ok=True)
    started = int(time.time() * 1000)
    slugs = ([store] if store else None) or stores or list(STOREFRONTS.keys())

    all_rows, runs, tot_new, tot_drop = [], [], 0, 0
    for slug in slugs:
        try:
            rows, total, warns = crawl_store(slug, cap=cap, log=log)
        except Exception as e:
            runs.append(_run(slug, 0, 0, "failed", ["crawl failed: %s" % str(e)[:140]], started))
            log("sevenfifty/%s: crawl failed: %s" % (slug, str(e)[:120]))
            continue
        # honest self-report: the API says there are items but we pulled none (or almost none) → drift
        if total > 0 and not rows:
            warns.append("meta.total=%d but 0 rows extracted — result schema drift" % total)
        if rows:
            fill = sum(1 for r in rows if r["sku"]) / len(rows)
            if fill < 0.5:
                warns.append("sku fill %d%% (<50%%) — result schema drift" % round(fill * 100))
        new, dropped = _snap_diff(rows, slug, state_dir)
        tot_new += new; tot_drop += dropped
        all_rows += rows
        status = "degraded" if warns else "success"
        runs.append(_run(slug, len(rows), new, status, warns, started, dropped))
        log("sevenfifty/%s (%s): %d items of %d reported (%d new, %d dropped) — %s"
            % (slug, STOREFRONTS.get(slug, slug), len(rows), total, new, dropped, status))

    if land and all_rows:
        try:
            import warehouse
            warehouse.write_accumulate(
                TABLE, all_rows,
                key=lambda r: "%s|%s" % (r["storefront"], r.get("sku") or r.get("sevenfifty_id")),
                fields=FIELDS)
            log("landed %s: +%d rows (accumulated)" % (TABLE, len(all_rows)))
        except Exception as e:
            log("%s land skipped: %s" % (TABLE, str(e)[:100]))

    header = ["Storefront", "SKU", "Name", "Producer", "Supplier", "Type", "Style", "Size", "Case"]
    prev = [[r["distributor"], r["sku"], r["name_full"] or r["name"], r["producer"], r["supplier"],
             r["product_type"], r["style"], r["size_formatted"], r["case_size"]] for r in all_rows]
    ds = {TABLE: {"header": header, "rows": prev[:1000], "total": len(all_rows), "_rows_full": prev}}
    return ds, runs, {"sampled": len(all_rows), "changed": tot_new, "dropped": tot_drop}


def main(argv=None):
    ap = argparse.ArgumentParser(description="SevenFifty/Provi distributor storefront catalogs (*.storefronts.site).")
    ap.add_argument("--store", help="one storefront slug (e.g. johnsonbrothers); default = all in STOREFRONTS")
    ap.add_argument("--cap", type=int, help="only crawl the first N pages (smoke test)")
    ap.add_argument("--no-land", action="store_true", help="parse only, don't write to the warehouse")
    ap.add_argument("--out", default=None, help="snapshot dir (default: agent_state/sevenfifty)")
    a = ap.parse_args(argv)
    ds, runs, mv = pull(store=a.store, cap=a.cap, land=not a.no_land, out=a.out, state_dir=a.out)
    r = list(ds.values())[0]
    for row in r["rows"][:15]:
        print("  •", "sku", row[1], "|", str(row[2])[:38], "|", str(row[4])[:20], "|", row[7], "x", row[8])
    print("runs:", [(x["extracts"][0]["id"], x["status"], x["total"], x["extracts"][0]["delta"]) for x in runs])
    print("movement:", mv)


if __name__ == "__main__":
    main()
