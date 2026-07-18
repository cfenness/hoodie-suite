#!/usr/bin/env python3
"""refresh_fast.py — FAST incremental coverage refresh: merge UberEats/Postmates store outlets straight into
src_outlets (write_accumulate by source|store_id), WITHOUT the full normalize rebuild (which re-queries every
source + dim_outlet, ~1min). Keeps the coverage map current on a tight cadence (~5min) while a crawl runs.

The map only needs source + geo per outlet; flags stay false for coverage-only stores (they have no products
yet — the deep crawl + a full normalize compute real flags later). Other sources' src_outlets rows are left
untouched. Run in a loop:  while true; do python -c "import refresh_fast; refresh_fast.run()"; sleep 300; done
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

# must match normalize.py's src_outlets schema exactly
FLD = ["source", "store_id", "store_name", "chain", "is_chain", "f_beer", "f_wine", "f_spirits", "f_hemp",
       "f_cannabis", "f_rtd_spirits", "flag_basis", "license_conflict", "address", "city", "state", "zip",
       "lat", "lng", "phone", "addr_valid", "hoodie_outlet", "name_key", "phone_norm", "addr_key",
       "geo_cell", "county_fips"]


def _row(site, r):
    lat, lng = r["lat"], r["lng"]
    return {"source": site, "store_id": r["store_uuid"], "store_name": r.get("store_name") or "",
            "chain": "", "is_chain": False, "f_beer": False, "f_wine": False, "f_spirits": False, "f_hemp": False,
            "f_cannabis": False, "f_rtd_spirits": False, "flag_basis": "none", "license_conflict": False,
            "address": r.get("street") or "", "city": r.get("city") or "", "state": r.get("state") or "",
            "zip": r.get("postal_code") or "",
            "lat": lat, "lng": lng, "phone": r.get("phone") or "", "addr_valid": True,
            "hoodie_outlet": "", "name_key": "", "phone_norm": "", "addr_key": "",
            "geo_cell": ("%.3f,%.3f" % (lat, lng)), "county_fips": ""}


def run(sites=("ubereats", "postmates"), log=print):
    t0 = time.time()
    rows = []
    for site in sites:
        try:
            for r in warehouse.query("%s_stores" % site,
                                     "SELECT store_uuid, store_name, lat, lng, state, city, postal_code, phone "
                                     "FROM t WHERE lat IS NOT NULL AND store_uuid <> ''"):
                rows.append(_row(site, r))
        except Exception as e:
            log("[refresh-fast] %s: %s" % (site, str(e)[:80]))
        # geofill (store-page JSON-LD): richer — has street address; keyed by the same URL id, so it OVERLAYS
        # the coverage-feed rows above (last write wins in write_accumulate) and adds the un-crawled universe.
        try:
            for r in warehouse.query("%s_geo" % site,
                                     "SELECT store_uuid, store_name, lat, lng, state, city, postal_code, phone, "
                                     "street FROM t WHERE lat IS NOT NULL AND store_uuid <> '' "
                                     "AND geo_status = 'ok'"):
                rows.append(_row(site, r))
        except Exception as e:
            log("[refresh-fast] %s_geo: %s" % (site, str(e)[:80]))
    if rows:
        warehouse.write_accumulate("src_outlets", rows, key=lambda r: (r["source"], r["store_id"]), fields=FLD)
    log("[refresh-fast] merged %d %s outlets -> src_outlets in %.1fs" % (len(rows), "+".join(sites), time.time() - t0))
    return len(rows)


if __name__ == "__main__":
    import kroger_api
    kroger_api._load_creds()
    run()
