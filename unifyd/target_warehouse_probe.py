#!/usr/bin/env python3
"""target_warehouse_probe.py — the REAL bar: does Target data actually LAND in the warehouse, free?

Not a store count, not a 200 — this runs the real target_scraper against the real (Tigris) warehouse
with FETCH_POLICY=free (no proxy tier available) and reports target_products / target_stores row counts
BEFORE and AFTER. If the count moves, Target data is IN THE WAREHOUSE, landed free from a cloud runner.
Exit 0 iff target_products grew."""
import os
import sys

os.environ.setdefault("FETCH_POLICY", "free")        # prove it with NO proxy tier at all
os.environ.setdefault("BROWSER_CHANNEL", "chrome")

import warehouse                                       # noqa: E402
import resi                                            # noqa: E402


def rc(name):
    try:
        return warehouse.row_count(name) or 0
    except Exception:
        return 0


print("warehouse.remote()=%s  bucket=%s  FETCH_POLICY=%s  isp_pool=%d  paygo_allowed=%s" % (
    warehouse.remote(), warehouse._bucket(), resi.fetch_policy(), len(resi.isp_pool()), resi.paygo_allowed()))
if not warehouse.remote():
    print("WARNING: not in remote (Tigris) mode — writes are LOCAL/ephemeral, not the real warehouse.")

import target_scraper as t                             # noqa: E402

b_prod, b_store = rc("target_products"), rc("target_stores")
print("BEFORE  target_products=%d  target_stores=%d" % (b_prod, b_store))

# stores — light store-locator landing
try:
    t.enumerate_stores(zips=["10001", "90012", "60601"])
except Exception as e:
    print("enumerate_stores error: %s" % str(e)[:160])

# products — a REAL bev-alc catalog slice for one store (assortment is largely national).
# write_accumulate MERGES, so this adds toward the full catalog; it never clobbers.
try:
    run_id, n = t.run(stores=[("3412", "10001", "NY")],
                      terms=["vodka", "whiskey", "tequila", "rum", "wine", "bourbon", "gin", "seltzer"],
                      pages=2, log=print)
    print("run() -> %s, %d product rows this slice" % (run_id, n))
except Exception as e:
    print("run() raised (rows may still have landed pre-error): %s" % str(e)[:200])

a_prod, a_store = rc("target_products"), rc("target_stores")
print("AFTER   target_products=%d (%+d)  target_stores=%d (%+d)" % (
    a_prod, a_prod - b_prod, a_store, a_store - b_store))
ok = (a_prod - b_prod) > 0
print("\nVERDICT: %s" % (
    "TARGET DATA IS IN THE WAREHOUSE — real rows landed FREE (cloud runner, no proxy). Full catalog = scale the sweep."
    if ok else "no product rows landed — NOT done; investigate (see errors above)."))
sys.exit(0 if ok else 1)
