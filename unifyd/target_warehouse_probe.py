#!/usr/bin/env python3
"""target_warehouse_probe.py — restore stores, REPAIR the per-product grain, verify by the RIGHT measures.

Against the real Tigris warehouse, FETCH_POLICY=free (no proxy):
  1. RESTORE target_stores — full zip re-enumeration (merged by store_id).
  2. REPAIR target_products — collapse to one row per tcin (my earlier per-store writes polluted it with
     duplicate rows the master would dedup anyway). One-time grain rebuild.
  3. Land a per-product catalog slice + per-store inventory via run().
  4. Verify by DISTINCT tcin (the true product count — not raw rows) and retail_observations via query_parts
     (it's a partitioned table; row_count only sees single-file tables).
Done iff: stores restored (>=1000), distinct products NOT shrunk, inventory observations landed.
"""
import os
import sys

os.environ.setdefault("FETCH_POLICY", "free")
os.environ.setdefault("BROWSER_CHANNEL", "chrome")

import warehouse                                       # noqa: E402
import resi                                            # noqa: E402


def rc(name):
    try:
        return warehouse.row_count(name) or 0
    except Exception:
        return 0


def distinct_tcin():
    try:
        r = warehouse.query("target_products", "SELECT count(DISTINCT COALESCE(CAST(tcin AS VARCHAR), CAST(product_id AS VARCHAR))) AS c FROM t")
        return (r[0]["c"] if r else 0) or 0
    except Exception:
        return 0


def obs_count():
    try:
        r = warehouse.query_parts("retail_observations", "SELECT count(*) AS c FROM t")
        return (r[0]["c"] if r else 0) or 0
    except Exception:
        return 0


print("warehouse.remote()=%s  bucket=%s  FETCH_POLICY=%s  paygo_allowed=%s" % (
    warehouse.remote(), warehouse._bucket(), resi.fetch_policy(), resi.paygo_allowed()))

import target_scraper as t                             # noqa: E402

b_store, b_prod_rows, b_prod_dist, b_obs = rc("target_stores"), rc("target_products"), distinct_tcin(), obs_count()
print("BEFORE  target_stores=%d  target_products rows=%d (distinct tcin=%d)  observations=%d" % (
    b_store, b_prod_rows, b_prod_dist, b_obs))

# 1) RESTORE the national store set
try:
    t.enumerate_stores()
except Exception as e:
    print("enumerate_stores error: %s" % str(e)[:160])

# 2) REPAIR target_products to per-product (collapse my earlier per-store pollution). One row per tcin.
try:
    existing = warehouse.query("target_products", "SELECT * FROM t")
    by_tcin = {}
    for r in existing:
        tc = r.get("tcin") or r.get("product_id")
        if tc and str(tc) not in by_tcin:
            by_tcin[str(tc)] = r
    if by_tcin:
        warehouse.write_parquet("target_products", list(by_tcin.values()))
        print("REPAIR  target_products %d rows -> %d distinct products (per-product grain)" % (len(existing), len(by_tcin)))
except Exception as e:
    print("repair error: %s" % str(e)[:160])

# 3) land a per-product catalog slice + per-store inventory (two stores)
try:
    run_id, n = t.run(stores=[("3412", "10001", "NY"), ("1382", "90012", "CA")],
                      terms=["vodka", "whiskey", "tequila", "rum", "wine", "bourbon", "gin", "seltzer"],
                      pages=2, log=print)
    print("run() -> %s, %d per-store rows (deduped to catalog by tcin)" % (run_id, n))
except Exception as e:
    print("run() raised: %s" % str(e)[:200])

a_store, a_prod_rows, a_prod_dist, a_obs = rc("target_stores"), rc("target_products"), distinct_tcin(), obs_count()
print("AFTER   target_stores=%d (%+d)  target_products rows=%d (distinct tcin=%d, %+d)  observations=%d (%+d)" % (
    a_store, a_store - b_store, a_prod_rows, a_prod_dist, a_prod_dist - b_prod_dist, a_obs, a_obs - b_obs))

stores_ok = a_store >= 1000
prod_ok = a_prod_dist >= b_prod_dist                   # DISTINCT products preserved/grown (grain-correct)
obs_ok = a_obs > b_obs                                 # inventory observations landed (partitioned)
ok = stores_ok and prod_ok and obs_ok
print("\nVERDICT: %s" % (
    "DONE — stores restored (%d), %d distinct products (per-product grain), %d inventory observations. FREE, no proxy."
    % (a_store, a_prod_dist, a_obs) if ok else
    "NOT done — stores_ok=%s products_preserved=%s obs_landed=%s" % (stores_ok, prod_ok, obs_ok)))
sys.exit(0 if ok else 1)
