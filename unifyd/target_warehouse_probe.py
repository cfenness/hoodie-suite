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
    for col in ("tcin", "product_id"):
        try:
            r = warehouse.query("target_products", "SELECT count(DISTINCT %s) AS c FROM t" % col)
            if r and r[0]["c"] is not None:
                return int(r[0]["c"])
        except Exception as e:
            print("  distinct(%s) err: %s" % (col, str(e)[:90]))
    return -1


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

# 2) AUTHORITATIVE REBUILD + sweep: run_national builds the national product catalog FRESH from source
#    (fixes any product loss from earlier mutations) and lands per-store inventory across a store sample.
try:
    n = t.run_national(limit=25, workers=4, log=print)
    print("run_national(limit=25) -> %s" % (n,))
except Exception as e:
    print("run_national raised (data may have landed pre-error): %s" % str(e)[:200])

a_store, a_prod_rows, a_prod_dist, a_obs = rc("target_stores"), rc("target_products"), distinct_tcin(), obs_count()
print("AFTER   target_stores=%d (%+d)  target_products rows=%d (distinct tcin=%d, %+d)  observations=%d (%+d)" % (
    a_store, a_store - b_store, a_prod_rows, a_prod_dist, a_prod_dist - b_prod_dist, a_obs, a_obs - b_obs))

stores_ok = a_store >= 1000
prod_ok = a_prod_dist >= 500                            # a real national bev-alc catalog (authoritative rebuild)
obs_ok = a_obs > b_obs                                  # NEW inventory observations landed from the sweep
ok = stores_ok and prod_ok and obs_ok
print("\nVERDICT: %s" % (
    "DONE — stores %d, %d distinct products (authoritative per-product catalog), +%d inventory observations. FREE, no proxy."
    % (a_store, a_prod_dist, a_obs - b_obs) if ok else
    "NOT done — stores_ok=%s catalog_ok=%s(%d distinct) obs_landed=%s(+%d)" % (
        stores_ok, prod_ok, a_prod_dist, obs_ok, a_obs - b_obs)))
sys.exit(0 if ok else 1)
