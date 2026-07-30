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
import argparse, gzip, hashlib, json, os, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe
import raw_capture

# binnys_products snapshot columns (the rich per-store cell → the master + hemp views read these).
# raw_json stays in the FULL shape (recs/PRODUCT_FIELDS) so nothing the source gives us is lost, but it
# does NOT land in the catalog write — see CATALOG_FIELDS below.
PRODUCT_FIELDS = ["sku", "store", "name", "brand", "varietal", "region", "origin", "category", "department",
                  "item_size", "unit_label", "case_pack", "proof", "abv", "thc_mg", "cbd_mg", "rating",
                  "reviews", "discount_pct", "deal_of_week", "is_sold_out", "in_store_only", "is_hemp",
                  "short_desc", "product_url", "image", "price", "qty", "raw_json"]
# What actually lands in binnys_products (write_accumulate AND write_full_rebuild). raw_json (up to 6KB,
# the whole Algolia hit) does NOT belong here: write_accumulate merges by rewriting the WHOLE table, so a
# fat payload column gets re-read and re-written on every flush, and gets more expensive as the crawl
# succeeds — the same "payload as a mutable catalog column" mistake raw_capture.py exists to fix,
# previously seen on ubereats_products and offprem_products. It's the reason a bucketize.py migration of
# this table (1.53M rows) was still producing tens of thousands of tiny partition files hours in — DuckDB's
# own fat-row heuristic throttles concurrency hard when rows are this wide. raw_capture keeps the payload,
# fully recoverable, just never re-read on a merge.
CATALOG_FIELDS = [f for f in PRODUCT_FIELDS if f != "raw_json"]

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
    """(records, complete). `complete` is False when pagination stopped early — a full crawl that
    lost a page is NOT the whole catalog, and the caller must not treat it as a rebuild."""
    if not crawl_all:
        return query(0, min(sample, 1000)).get("hits", []), False
    first = query(0, 1000)
    out = list(first.get("hits", []))
    pages = min(first.get("nbPages", 1), 100)   # was 40 — don't truncate the full Algolia catalog
    complete = True
    for p in range(1, pages):
        try:
            out += query(p, 1000).get("hits", [])
        except Exception as e:
            log(f"page {p}: {e}")
            complete = False
            break
        log(f"page {p + 1}/{pages}: {len(out)} records")
    return out, complete


def _store_price(sp):
    pr = sp.get("prices") or {}
    sale = pr.get("salePrice")
    if sp.get("isOnSale") and sale not in (None, 0):
        return float(sale)
    reg = pr.get("regularPrice")
    try: return float(reg) if reg is not None else None
    except (ValueError, TypeError): return None


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None



# ── day-over-day snapshot ────────────────────────────────────────────────────────────────────────
# Lives in the SHARED bucket, gzipped, compact. Three things were wrong with a local pretty-printed
# file, and each one bit:
#   1. DISK. 1.5M cells at indent=2 is multiple GB of whitespace-padded JSON. On an ephemeral machine
#      that is `OSError: [Errno 28] No space left on device` — the actual cause of every recent
#      binnys failure.
#   2. EPHEMERALITY. The file died with the machine, so `prev` was always empty and every run
#      reported "baseline (no prior snapshot)". Movement — the entire point of a directional tracker
#      — has never once been computed in the cloud.
#   3. ORDER. It was written BEFORE the warehouse land, so a failure here discarded a completed
#      fetch. Bookkeeping must never be able to destroy the payload.
_SNAP_KEY = "_collect/snapshots/binnys.json.gz"


def _snap_load(state_dir, log=print):
    """Prior snapshot cells: shared bucket first, local file as the CLI fallback. {} if neither."""
    try:
        import warehouse
        raw = warehouse.get_bytes(_SNAP_KEY)
        if raw:
            cells = json.loads(gzip.decompress(raw)).get("cells", {})
            log("  [binnys] prior snapshot: %s cells (shared bucket)" % f"{len(cells):,}")
            return cells
    except Exception as e:
        log("  [binnys] snapshot read skipped: %s" % str(e)[:80])
    fp = os.path.join(state_dir, SNAP)
    if os.path.exists(fp):
        try:
            return json.load(open(fp)).get("cells", {})
        except Exception:
            pass
    return {}


def _snap_save(cells, log=print):
    """Persist the snapshot compactly (no indent) and gzipped, to the shared bucket."""
    try:
        import warehouse
        blob = gzip.compress(json.dumps({"__ts__": int(time.time() * 1000), "cells": cells},
                                        separators=(",", ":")).encode())
        warehouse.put_bytes(_SNAP_KEY, blob)
        log("  [binnys] snapshot saved: %s cells, %.1f MB gz" % (f"{len(cells):,}", len(blob) / 1e6))
    except Exception as e:
        log("  [binnys] snapshot save skipped: %s" % str(e)[:100])


def to_snapshot(records):
    """Per-store cells keyed `sku|storeCode`. TAKE EVERYTHING the Algolia hit gives — it carries 57 fields, not
    just name+price: proof (→ABV), THC/CBD mg per serving/unit (the dose the hemp vendor is 19-100% null on),
    case pack, unit label, ratings, deal/discount, descriptions, availability — all structured on the record."""
    snap, parsed_qty = {}, 0
    for h in records:
        sku = str(h.get("objectID") or "")
        if not sku:
            continue
        proof = _num(h.get("proof"))
        thc = _num(h.get("thcMgPerServing")) or _num(h.get("thcMgPerUnit")) or _num(h.get("thcMgPerSellPack"))
        cbd = _num(h.get("cbdMgPerServing")) or _num(h.get("cbdMgPerUnit")) or _num(h.get("cbdMgPerSellPack"))
        name = h.get("productName") or ""
        rich = {"name": name, "brand": h.get("productBrandName") or "",
                "varietal": h.get("productVarietal") or "", "region": h.get("region") or "",
                "origin": h.get("country") or "", "category": h.get("productType") or h.get("gtmCategory") or "",
                "department": h.get("productDepartment") or "",
                "item_size": h.get("itemSize") or "", "unit_label": h.get("priceUnitLabel") or "",
                "case_pack": _num(h.get("casePack")), "image": h.get("imageUrl") or "",
                "product_url": h.get("productUrl") or h.get("productLink") or "",
                "proof": proof, "abv": (round(proof / 2, 1) if proof else None),
                "thc_mg": thc, "cbd_mg": cbd,
                "rating": _num(h.get("ratingNumber")), "reviews": _num(h.get("reviewsAmount")),
                "discount_pct": _num(h.get("pricePercentDiscount")),
                "deal_of_week": bool(h.get("isDealOfTheWeek")),
                "is_sold_out": bool(h.get("isSoldOut")), "in_store_only": bool(h.get("isInStoreOnly")),
                "short_desc": (h.get("shortDescription") or "")[:500],
                "is_hemp": observe.is_hemp(name, h.get("productType") or "") or bool(thc or cbd),
                # keep the WHOLE Algolia hit — capture everything the source serves, not just what we promote
                "raw_json": json.dumps({k: v for k, v in h.items() if k != "_highlightResult"},
                                       separators=(",", ":"))[:6000]}
        for sp in (h.get("storesPriceAndInventory") or []):
            store = str(sp.get("storeCode") or "")
            if not store:
                continue
            qty = sp.get("purchaseAvailability")
            qty = int(qty) if isinstance(qty, (int, float)) else None
            if qty is not None:
                parsed_qty += 1
            snap[f"{sku}|{store}"] = dict(rich, price=_store_price(sp), qty=qty, store=store, sku=sku)
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


REPLACE_FLOOR = float(os.environ.get("BINNYS_REPLACE_FLOOR", "0.9"))


def _safe_to_replace(n_new, log=print):
    """May a full crawl REPLACE the landed catalog? Only if it isn't materially smaller than what is
    already there. A shrink means the crawl came up short, not that Binny's delisted 90% of its range."""
    try:
        prior = warehouse.row_count("binnys_products")
    except Exception as e:                       # noqa: BLE001 — can't verify ⇒ don't replace
        log("  [binnys] cannot read prior row count (%s) — accumulating instead of replacing" % str(e)[:60])
        return False
    if prior and n_new < prior * REPLACE_FLOOR:
        log("  [binnys] REFUSING to replace catalog: %d new rows vs %d landed (floor %.0f%%) — "
            "accumulating instead" % (n_new, prior, REPLACE_FLOOR * 100))
        return False
    return True


def pull(sample=300, crawl_all=False, limit=None, out=".", state_dir=None, log=print):
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    crawl_complete = False
    try:
        records, crawl_complete = fetch_records(sample=sample, crawl_all=crawl_all, log=log)
    except Exception as e:
        run = run_record(diff_store({}, {}), 0, "failed",
                         [f"Algolia query failed: {str(e)[:120]} — the public search key may have "
                          "rotated; re-discover it from a binnys.com product page."])
        return {}, [run], run["movement"]
    if limit:
        records = records[:limit]
        crawl_complete = False          # an explicit truncation is not a complete catalog
    cur, parsed_qty = to_snapshot(records)
    if not cur:
        run = run_record(diff_store({}, {}), 0, "failed",
                         ["Algolia returned no per-store inventory (storesPriceAndInventory schema changed)."])
        return {}, [run], run["movement"]

    prev = _snap_load(state_dir, log=log)
    movement = diff_store(prev, cur)

    warnings = []
    if parsed_qty / len(cur) < 0.5:
        warnings.append(f"Numeric per-store qty present on only {parsed_qty}/{len(cur)} cells — "
                        "Binny's storesPriceAndInventory schema may have changed.")
    status = "degraded" if warnings else "success"

    # land the rich per-store cells to the warehouse (the master + hemp views read binnys_products) + the
    # dated observation time-series (qty = exact on-hand; the whole reason Binny's is a Counts source).
    try:
        recs = [{k: v.get(k) for k in PRODUCT_FIELDS} for v in cur.values()]
        # raw_json goes to the append-only capture, never the catalog write (see CATALOG_FIELDS above).
        raw_capture.record("binnys", time.strftime("%Y-%m-%d"), "products",
                           [{"entity_id": "%s|%s" % (r.get("sku"), r.get("store")), "parent_id": r.get("sku"),
                             "raw_json": r.get("raw_json")} for r in recs], log=log)
        lean = [{k: v for k, v in r.items() if k != "raw_json"} for r in recs]
        # a COMPLETE full crawl is the whole catalog -> OVERWRITE (a clean, consistently-typed schema);
        # merging into the old narrower table with write_accumulate hits a pyarrow type conflict (old
        # string cols vs new numeric). Anything else accumulates so it grows rather than clobbers.
        #
        # `crawl_all` alone is NOT sufficient authority to replace a catalog: pagination breaks out on a
        # page error and --limit truncates, so a full crawl can return a few hundred rows of a 29,787-SKU
        # table. That write would not be EMPTY, so warehouse's empty-write guard would not catch it. Hence
        # both a completeness flag and a row-count floor.
        if crawl_all and crawl_complete and _safe_to_replace(len(recs), log):
            # write_full_rebuild, not write_parquet: binnys_products is bucketed (the OOM fix — the
            # accumulate branch below was read-modify-writing all 1.5M rows on every incremental flush,
            # and that cost grows every run) — a plain write_parquet would raise on this branch instead.
            warehouse.write_full_rebuild("binnys_products", lean, fields=CATALOG_FIELDS)
        else:
            warehouse.write_accumulate("binnys_products", lean, key=lambda r: (r["sku"], r["store"]),
                                       fields=CATALOG_FIELDS)
        observe.record("binnys", [{"source": "binnys", "store_id": v["store"], "store": "Binny's #%s" % v["store"],
                                   "product_id": v["sku"], "upc": "", "brand": v["brand"], "name": v["name"],
                                   "price": v["price"], "on_promo": bool(v.get("deal_of_week") or v.get("discount_pct")),
                                   "in_stock": (v.get("qty") or 0) > 0, "qty": v.get("qty"),
                                   "is_hemp": v.get("is_hemp")} for v in cur.values()],
                       log=log)
    except Exception as e:
        log("  [binnys] warehouse land skipped: %s" % str(e)[:80])
    # Snapshot LAST — after the data is safely landed, so a snapshot failure costs tomorrow's diff
    # rather than today's catalog.
    _snap_save(cur, log=log)
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
