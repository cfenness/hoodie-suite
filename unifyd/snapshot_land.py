"""snapshot_land.py — land the built scrapers' LOCAL snapshots into the warehouse.

Several store-level scrapers (abc / specs / binnys / shopify) were built before the warehouse-sync
pattern and wrote per-store {price, in-stock, qty} to agent_state/<conn>/<conn>_snapshot.json instead
of the warehouse — so they never fed it. This normalizes each snapshot to a common shape and lands it
as `<conn>_products` (+ a `<conn>_runs` row), so every chain we already have shows up in the warehouse
and the tracker. Run it standalone or import land_all() from the orchestrator.
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

_ROOT = os.path.dirname(os.path.abspath(__file__))
CHAINS = {   # conn dir -> (chain label, warehouse dataset name)
    "abc":     ("ABC Fine Wine & Spirits", "abc_products"),
    "specs":   ("Spec's",                  "specs_products"),
    "binnys":  ("Binny's Beverage Depot",  "binnys_products"),
    "shopify": ("Shopify DTC brands",      "shopify_products"),
}


def log(*a): print(*a, file=sys.stderr, flush=True)


def _norm(cell, chain, ts):
    instock = cell.get("instock")
    qty = cell.get("qty")
    if instock is None and qty is not None:
        instock = qty > 0
    row = dict(chain=chain, sku=str(cell.get("sku", "")), store=str(cell.get("store", "")),
               name=(cell.get("name") or cell.get("title") or ""), brand=(cell.get("brand") or ""),
               price=cell.get("price"), qty=qty, in_stock=bool(instock), pulled_at=ts)
    # carry the rich fields the enriched scrapers capture — ALWAYS include them (empty string default) so the
    # column exists in the table even when sparse; conditional inclusion dropped sparse fields at schema-infer.
    for k in ("varietal", "region", "sub_region", "appellation", "origin", "category", "item_size",
              "upc", "abv", "description"):
        row[k] = cell.get(k) or ""
    return row


def land(conn):
    chain, table = CHAINS[conn]
    path = os.path.join(_ROOT, "agent_state", conn, "%s_snapshot.json" % conn)
    if not os.path.exists(path):
        log("  %-8s no snapshot at %s" % (conn, path)); return 0
    snap = json.load(open(path))
    ts = int(snap.get("__ts__", 0)) // 1000 or int(time.time())
    rows = [_norm(v, chain, ts) for v in snap.get("cells", {}).values()]
    if not rows:
        log("  %-8s snapshot empty" % conn); return 0
    warehouse.write_parquet(table, rows)
    # dated time-series (price + inventory per store) — track changes day over day
    import observe
    obs_date = time.strftime("%Y-%m-%d", time.gmtime(ts))
    observe.record(conn, [dict(store=r["store"], store_id=r["store"], product_id=r["sku"],
                               brand=r["brand"], name=r["name"], price=r["price"],
                               in_stock=r["in_stock"], qty=r.get("qty"),
                               is_hemp=observe.is_hemp(r["brand"], r["name"])) for r in rows],
                   date=obs_date, log=log)
    skus = len({r["sku"] for r in rows}); stores = len({r["store"] for r in rows})
    oos = sum(1 for r in rows if not r["in_stock"])
    runs = []
    try:
        runs = warehouse.query(conn + "_runs", "SELECT * FROM t")
    except Exception:
        pass
    runs.append(dict(run_id="%s-%s" % (conn, time.strftime("%Y%m%d", time.gmtime(ts))), at=ts,
                     cells=len(rows), skus=skus, stores=stores, in_stock=len(rows) - oos,
                     status="ok", note="landed from local snapshot"))
    warehouse.write_parquet(conn + "_runs", runs[-50:])
    log("  %-8s %5d cells · %4d SKUs · %3d stores (%d in-stock) -> %s" % (conn, len(rows), skus, stores, len(rows) - oos, table))
    return len(rows)


def land_all():
    total = 0
    for conn in CHAINS:
        try:
            total += land(conn)
        except Exception as e:
            log("  %-8s FAILED: %s" % (conn, str(e)[:80]))
    log("landed %d store-cells across %d connectors" % (total, len(CHAINS)))
    return total


if __name__ == "__main__":
    land_all()
