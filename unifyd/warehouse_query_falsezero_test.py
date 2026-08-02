"""Regression test for the plain query() FALSE ZERO: python warehouse_query_falsezero_test.py

query_parts() was already hardened against this (see warehouse_falsezero_test.py), but the
single-file query() path — used by far more callers, including ue_catalog.universe(), the
completeness DENOMINATOR for every UberEats/Postmates sweep — had zero retry protection. Observed
live 2026-08-02: 6 of 8 UberEats shards restarted within the same instant, each independently
called universe() against the same large `ubereats_sitemap` table, and Tigris returned "Server
returned nothing (no headers, no data)" for most of them — a thundering herd, not a real empty
table. universe() reported 0 stores, and every one of those 6 shards exited with status=failed,
discarding a live process for a transient blip that a retry would have cost seconds to clear.

Contract this pins (mirrors query_parts()'s contract exactly):
  1. transient read, then success   → the rows    (retried, not swallowed)
  2. transient every time           → RAISES      (loud; the caller degrades explicitly)
  3. non-transient error            → RAISES      (never a false zero)
  4. the SCAN is covered too, not just the view creation
  5. a bucketed (v2) table with no active parts   → []   (documented empty-safe case, unchanged)

Fake connection, no duckdb, no network — stdlib only.
"""
import os
import sys
import time

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET",
          "PLACES_BUCKET"):
    os.environ.pop(v, None)
os.environ["WAREHOUSE_WRITE_RETRIES"] = "3"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

time.sleep = lambda *_a, **_k: None      # don't actually back off in a unit test
warehouse.read_manifest = lambda name: None   # no v2 manifest — exercise the plain single-file path

passed = failed = 0


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print("  ok   %s" % name)
    else:
        failed += 1
        print("  FAIL %s" % name)


class FakeCursor:
    description = [("c",)]

    def fetchall(self):
        return [(42,)]


class FakeCon:
    """Raises `errors` (in order) from execute(); a None entry means succeed."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def execute(self, sql, params=None):
        self.calls += 1
        err = self.errors.pop(0) if self.errors else None
        if err:
            raise Exception(err)
        return FakeCursor()


def run(errors):
    con = FakeCon(errors)
    warehouse.connect = lambda: con
    return con


TRANSIENT = "IO Error: Server returned nothing (no headers, no data) error for HTTP GET to " \
            "'https://fly.storage.tigris.dev/x.parquet'"
REAL_BUG = "Binder Error: Referenced column \"nope\" not found in FROM clause!"

print("1. transient read then success → the rows, not a false zero")
con = run([TRANSIENT, TRANSIENT, None, None])     # view fails twice, then view + scan succeed
got = warehouse.query("t1", "SELECT count(*) c FROM t")
ok("rows came back after retry (got %r)" % got, got == [{"c": 42}])
ok("it actually retried (execute called %d times)" % con.calls, con.calls >= 3)

print("\n2. transient every time → RAISES (loud, so the caller can degrade)")
run([TRANSIENT] * 10)
try:
    warehouse.query("t2", "SELECT 1")
    ok("should have raised", False)
except Exception as e:
    ok("raised instead of returning [] (%s…)" % str(e)[:34], "Server returned nothing" in str(e))

print("\n3. a genuine SQL bug → RAISES, never a silent zero")
run([REAL_BUG])
try:
    warehouse.query("t3", "SELECT nope FROM t")
    ok("should have raised", False)
except Exception as e:
    ok("raised (%s…)" % str(e)[:30], "Binder Error" in str(e))

print("\n4. the SCAN is covered too, not just the view creation")
con = run([None, TRANSIENT, None])                 # view ok, scan blips once, then ok
got = warehouse.query("t4", "SELECT count(*) c FROM t")
ok("scan retried and returned rows (got %r)" % got, got == [{"c": 42}])

print("\n5. a bucketed table with no active parts → [] (documented empty-safe case, unchanged)")
warehouse.read_manifest = lambda name: {"layout": "bucketed", "parts": {}}
run([])
ok("returns empty, does not raise", warehouse.query("t5", "SELECT 1") == [])
warehouse.read_manifest = lambda name: None

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
