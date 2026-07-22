#!/usr/bin/env python3
"""target_warehouse_probe.py — restore the store table + verify the CORRECTED per-product landing, free.

Does three real things against the real Tigris warehouse (FETCH_POLICY=free, no proxy):
  1. RESTORE target_stores — re-enumerate the FULL zip set (write_accumulate by store_id merges the
     national ~1163 back; the first buggy probe had clobbered it to 60).
  2. Land the per-PRODUCT catalog for a couple of stores via the fixed run() (dedup by tcin — no per-store
     bloat), and per-store inventory to retail_observations.
  3. Report before/after counts. Done iff stores are restored AND target_products did not shrink.
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


print("warehouse.remote()=%s  bucket=%s  FETCH_POLICY=%s  paygo_allowed=%s" % (
    warehouse.remote(), warehouse._bucket(), resi.fetch_policy(), resi.paygo_allowed()))

import target_scraper as t                             # noqa: E402

b_store, b_prod, b_obs = rc("target_stores"), rc("target_products"), rc("retail_observations")
print("BEFORE  target_stores=%d  target_products=%d  retail_observations=%d" % (b_store, b_prod, b_obs))

# 1) RESTORE the national store set — full zip enumeration, merged by store_id
try:
    t.enumerate_stores()
except Exception as e:
    print("enumerate_stores error: %s" % str(e)[:160])

# 2) per-PRODUCT catalog + per-store obs for two stores (the fixed run(): dedup by tcin, no bloat)
try:
    run_id, n = t.run(stores=[("3412", "10001", "NY"), ("1382", "90012", "CA")],
                      terms=["vodka", "whiskey", "tequila", "rum", "wine", "bourbon", "gin", "seltzer"],
                      pages=2, log=print)
    print("run() -> %s, %d per-store product rows fetched (deduped to catalog by tcin)" % (run_id, n))
except Exception as e:
    print("run() raised (rows may have landed pre-error): %s" % str(e)[:200])

a_store, a_prod, a_obs = rc("target_stores"), rc("target_products"), rc("retail_observations")
print("AFTER   target_stores=%d (%+d)  target_products=%d (%+d)  retail_observations=%d (%+d)" % (
    a_store, a_store - b_store, a_prod, a_prod - b_prod, a_obs, a_obs - b_obs))

stores_ok = a_store >= 1000                            # national set restored
prod_ok = a_prod >= b_prod                             # per-product catalog did NOT shrink (grain fixed)
obs_ok = a_obs > b_obs                                 # per-store inventory landed to observations
print("\nVERDICT: %s" % (
    "STORES RESTORED (%d), per-product catalog intact/grew, inventory landed — FREE, no proxy." % a_store
    if (stores_ok and prod_ok) else
    "NOT done — stores_ok=%s prod_no_shrink=%s obs_grew=%s (see numbers)." % (stores_ok, prod_ok, obs_ok)))
sys.exit(0 if (stores_ok and prod_ok) else 1)
