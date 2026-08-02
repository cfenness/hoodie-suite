"""Regression test for the `chain` subtag on retail_observations: python observe_chain_test.py

Before this, a source that sweeps multiple retail banners under one connector identity (doordash_full.py
prices Walmart/Target/etc through DoorDash's own storefront) had to tag rows `source=<chain>` (e.g.
"walmart") — DoorDash's own contribution became unrecoverable, blended into whatever else also writes
source="walmart" with no way to tell them apart. Now `source` stays the connector's own identity
("doordash") and `chain` is an optional per-row subtag.

Contract this pins:
  1. record() lands the `chain` field from each row, not just the fixed top-level fields
  2. a row with no `chain` key lands "" (empty), not a KeyError
  3. OBS_FIELDS carries `chain` so query_parts()'s union_by_name sees it across all partitions

Fake warehouse.write_partition, no duckdb, no network — stdlib only.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import observe

passed = failed = 0


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print("  ok   %s" % name)
    else:
        failed += 1
        print("  FAIL %s" % name)


landed = {}


def fake_write_partition(name, part, records, fields=None, dtypes=None):
    landed["name"], landed["part"], landed["records"], landed["fields"] = name, part, records, fields


observe.warehouse.write_partition = fake_write_partition

print("1. record() lands the chain subtag per row")
observe.record("doordash", [dict(chain="walmart", store="Walmart #123", store_id="w123", product_id="p1")],
               date="2026-08-02", part="2026-08-02_doordash_walmart")
ok("source is the connector's own identity", landed["records"][0]["source"] == "doordash")
ok("chain carries the retailer being priced", landed["records"][0]["chain"] == "walmart")
ok("part is the caller-supplied unique key, not the source-collision default",
   landed["part"] == "2026-08-02_doordash_walmart")

print("\n2. a row with no chain key lands empty, not a crash")
observe.record("publix", [dict(store="Publix #4", store_id="p4", product_id="i1")], date="2026-08-02")
ok("no exception, chain defaults empty", landed["records"][0]["chain"] == "")

print("\n3. OBS_FIELDS carries chain for query_parts()'s union_by_name")
ok("chain is a declared column", "chain" in observe.OBS_FIELDS)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
