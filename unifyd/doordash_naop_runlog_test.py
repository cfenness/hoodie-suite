#!/usr/bin/env python3
"""doordash_naop_runlog_test.py — naop must be visible in scrape_runs, or "daily cadence" is a lie.

Grounded in a real gap: doordash_naop.py's run() never called runlog.track() at all — unlike
doordash_chains.py (and every other registry source that wraps itself this way), so it had ZERO entries
in scrape_runs despite being registered enabled=True, cadence=daily. That makes it indistinguishable
from a source that never actually runs: due-ness bookkeeping and /api/jobs both read scrape_runs, and a
source that never writes there is invisible to both, whether or not the dispatcher is calling it. Fixed
by wrapping the scrape phase in runlog.track("naop", ...), the same mechanism every other tracked
source uses.

No real network call — _pull_one is monkeypatched to return canned data instantly, same discipline as
the rest of this suite. runlog writes to scrape_runs via warehouse.write_partition (one file per run —
see runlog.py's own comment on why), so this reads it back with query_parts, not query.

    python3 unifyd/doordash_naop_runlog_test.py
"""
import os
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def main():
    import warehouse
    warehouse._LOCAL_DIR = tempfile.mkdtemp(prefix="naop_runlog_")
    import doordash_naop as naop

    def fake_pull_one(sid, run_id):
        return ([{"store": sid, "account": "Test Bar %s" % sid, "cuisine": "american", "cuisines": "american",
                  "name": "Old Fashioned", "description": "", "price": 12.0, "price_basis": "doordash_delivery",
                  "category": "cocktail", "is_alcoholic": True, "root": "Old Fashioned", "sub": "",
                  "base_spirit": "bourbon", "beer_style": "", "is_hemp": False, "run_id": run_id}],
                dict(store=sid, account="Test Bar %s" % sid, cuisine="american", cuisines="american",
                     cuisine_source="detected", clean_name="Test Bar %s" % sid, street="", city="", state="",
                     phone="", serves_alcohol=True, n_beverages=1, run_id=run_id))

    real_pull_one = naop._pull_one
    naop._pull_one = fake_pull_one
    try:
        print("run() registers in scrape_runs via runlog — the property that was entirely missing")
        before = len(warehouse.query_parts("scrape_runs", "SELECT * FROM t WHERE connId = 'naop'"))
        n = naop.run(stores=["1001", "1002", "1003"])
        after = warehouse.query_parts("scrape_runs", "SELECT * FROM t WHERE connId = 'naop'")
        check("run() actually landed beverage rows", n == 3, n)
        check("a NEW scrape_runs entry exists for naop after run()", len(after) == before + 1, (before, len(after)))
        rec = after[-1]
        check("the run is recorded as done, not left running or silently absent",
              rec["status"] == "done", rec)
        check("progress reflects all 3 stores processed", rec["n"] == 3 and rec["total"] == 3, rec)

        print("\nreal data still lands in naop_beverages / naop_accounts as before — the fix didn't "
              "change what gets captured, only whether the run is visible")
        bevs = warehouse.query("naop_beverages", "SELECT * FROM t")
        accts = warehouse.query("naop_accounts", "SELECT * FROM t")
        check("3 beverages landed", len(bevs) == 3, len(bevs))
        check("3 accounts landed", len(accts) == 3, len(accts))
    finally:
        naop._pull_one = real_pull_one

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
