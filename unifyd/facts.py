"""facts.py — normalized, DATED inventory + pricing fact tables, keyed to product + outlet.

`retail_observations` is the raw per-store landing: one wide row per product x store x day (price, qty, in/out).
This derives the two star-schema facts the analytics layer wants, each dated and FK'd to the dimensions:

  • fact_price      — date, store_key, source, store_id, product_id, upc, sku_key, price, on_promo, promo
  • fact_inventory  — date, store_key, source, store_id, product_id, upc, sku_key, qty, in_stock, stock_level

plus `dim_store` — the RETAIL-CHAIN outlet dimension (store_key <- source|store_id): the chain stores
(Total Wine #920, ABC #003, Spec's …) that the license-based dim_outlet_resolved never carried. Best-effort
geo from a per-source store table when one exists.

Both facts are partitioned one file per (date, source) — same as retail_observations — so a re-run rebuilds
only the touched slices and history accumulates for over-time tracking.

Product link: (source, product_id) + upc. sku_key is resolved via upc -> dim_sku today (most sources carry no
UPC, so it's often null); the master's source->sku crosswalk will fill the rest (TODO: emit from the build).

    python facts.py                 # refresh every (date, source) slice
    python facts.py --source abc    # just one source
"""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
try:
    from sku_match import norm_upc          # GS1-aware canonical core, so obs UPCs match the master's form
except Exception:
    def norm_upc(u):
        return str(u or "").lstrip("0") or None

STORE_FIELDS = ["store_key", "source", "store_id", "store_name", "chain", "city", "state", "zip", "lat", "lng"]
PRICE_FIELDS = ["date", "source", "store_key", "store_id", "product_id", "upc", "sku_key",
                "price", "on_promo", "promo"]
INV_FIELDS = ["date", "source", "store_key", "store_id", "product_id", "upc", "sku_key",
              "qty", "in_stock", "stock_level"]

_OBS_COLS = 'SELECT "date", source, store, store_id, product_id, upc, price, on_promo, promo, ' \
            'qty, in_stock, stock_level FROM t'


def store_key(source, store_id):
    return hashlib.md5(("%s|%s" % (source, store_id)).encode()).hexdigest()[:16]


def _upc_sku_map(log):
    """upc -> sku_key from the master (rows with a real UPC only). Most observations lack a UPC, so this
    resolves the minority now; the rest await the master's source->sku crosswalk."""
    m = {}
    try:
        for r in warehouse.query("dim_sku", "SELECT upc, sku_key FROM t WHERE upc IS NOT NULL AND upc <> ''"):
            k = norm_upc(r["upc"])
            if k:
                m[k] = r["sku_key"]
    except Exception as e:
        log("  [facts] upc->sku map unavailable: %s" % str(e)[:60])
    return m


def _geo_for(source):
    """best-effort {store_id: {city,state,zip,lat,lng,chain}} from a per-source store table, if one exists."""
    for t in ("%s_stores" % source, "%s_outlets" % source, source.replace("-", "") + "_outlets",
              source.replace("-", "") + "_stores"):
        try:
            rows = warehouse.query(t, "SELECT * FROM t")
        except Exception:
            continue
        out = {}
        for r in rows:
            sid = str(r.get("store_id", "") or "")
            if sid:
                out[sid] = {"city": r.get("city"), "state": r.get("state"), "zip": r.get("zip"),
                            "lat": r.get("lat"), "lng": r.get("lng") if r.get("lng") is not None else r.get("lon"),
                            "chain": r.get("chain") or r.get("name")}
        if out:
            return out
    return {}


def build(source=None, log=print):
    umap = _upc_sku_map(log)
    where = 'WHERE source = ?' if source else ''
    slices = warehouse.query_parts("retail_observations",
                                   'SELECT DISTINCT "date", source FROM t %s' % where,
                                   [source] if source else None)
    log("[facts] %d (date,source) slices to build%s" % (len(slices), " for " + source if source else ""))
    stores, np_tot, ni_tot = {}, 0, 0
    for sl in slices:
        d, src = sl["date"], sl["source"]
        geo = _geo_for(src)
        rows = warehouse.query_parts("retail_observations", _OBS_COLS + ' WHERE "date"=? AND source=?', [d, src])
        prices, invs = [], []
        for r in rows:
            sid = str(r.get("store_id", "") or "")
            sk = store_key(src, sid)
            if sk not in stores:
                g = geo.get(sid, {})
                stores[sk] = {"store_key": sk, "source": src, "store_id": sid,
                              "store_name": r.get("store") or g.get("chain") or "", "chain": g.get("chain") or "",
                              "city": g.get("city"), "state": g.get("state"), "zip": g.get("zip"),
                              "lat": g.get("lat"), "lng": g.get("lng")}
            upc = str(r.get("upc") or "")
            skukey = umap.get(norm_upc(upc)) if upc else None
            base = {"date": d, "source": src, "store_key": sk, "store_id": sid,
                    "product_id": str(r.get("product_id") or ""), "upc": upc, "sku_key": skukey}
            invs.append({**base, "qty": r.get("qty"), "in_stock": bool(r.get("in_stock")),
                         "stock_level": r.get("stock_level") or ""})
            if r.get("price") is not None:
                prices.append({**base, "price": r.get("price"),
                               "on_promo": bool(r.get("on_promo")), "promo": r.get("promo")})
        part = "%s_%s" % (d, src)
        if invs:
            warehouse.write_partition("fact_inventory", part, invs, INV_FIELDS)
        if prices:
            warehouse.write_partition("fact_price", part, prices, PRICE_FIELDS)
        np_tot += len(prices)
        ni_tot += len(invs)
        log("  [facts] %-22s inv=%d price=%d" % (part, len(invs), len(prices)))
    if stores:
        warehouse.write_accumulate("dim_store", list(stores.values()), key=lambda s: s["store_key"], fields=STORE_FIELDS)
    log("[facts] DONE: %d stores · %d inventory · %d price rows -> dim_store / fact_inventory / fact_price"
        % (len(stores), ni_tot, np_tot))
    return len(stores), ni_tot, np_tot


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="", help="only this source (else all)")
    a = ap.parse_args()
    build(source=a.source or None)
