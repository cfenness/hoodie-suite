"""Regression test for the query_parts FALSE ZERO: python warehouse_falsezero_test.py

`query_parts` used to be a bare `except Exception: return []` around the view creation, so a
dropped Tigris read came back as an empty result set that no caller could tell apart from "this
table has no data". Measured live on 2026-07-28: the identical Tito's query returned [] twice and
then 6,696 rows, because one part file's footer read blipped. 14 modules call this function —
coverage, facts, monitor, run_sources, normalize, build_item_identity, the locator — so every one
of them could silently read a zero that wasn't there. Same class as the row-count false zero that
previously faked empty runs, and the exact "quiet degrade" that is worse than a crash.

Contract this pins:
  1. genuinely no partitions        → []          (the documented empty-safe case, unchanged)
  2. transient read, then success   → the rows    (retried, not swallowed)
  3. transient every time           → RAISES      (loud; the caller degrades explicitly)
  4. non-transient error            → RAISES      (never a false zero)
  5. the scan is covered, not just the view creation
  6. a blipped LISTING behaves the same way       (added 2026-08-03)

Case 6 exists because query_parts no longer hands DuckDB a `<dir>/*.parquet` glob — that path
corrupts the read on a large partition set (retail_observations: 3,824 files, reproduced 4/4, raw
Parquet bytes returned where a string was expected). It now resolves the file list itself and passes
an explicit list. That moves the read's first step onto a LIST call, which is a NEW false-zero
surface: a dropped Tigris listing would read as "this table has no data". So the listing is retried
and raises, exactly like the scan.

Fake connection + a real temp partition dir, no duckdb, no network — stdlib only.
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


def run(errors, files=("p1.parquet", "p2.parquet")):
    """Point query_parts at a fake connection AND a non-empty partition listing.

    The listing has to be non-empty or query_parts short-circuits to [] before it ever touches the
    connection — which is correct behaviour for a table with no partitions, but would make cases
    2-5 vacuous."""
    con = FakeCon(errors)
    warehouse.connect = lambda: con
    warehouse._partition_files_strict = lambda name: list(files)
    return con


TRANSIENT = "IO Error: Failed to read connection error for HTTP GET to 'https://fly.storage.tigris.dev/x.parquet'"
NO_FILES = "IO Error: No files found matching the pattern"
REAL_BUG = "Binder Error: Referenced column \"nope\" not found in FROM clause!"

print("1. genuinely no partitions → [] (documented empty-safe case)")
run([NO_FILES], files=())
ok("returns empty, does not raise", warehouse.query_parts("t1", "SELECT 1") == [])

print("\n2. transient read then success → the rows, not a false zero")
con = run([TRANSIENT, TRANSIENT, None, None])     # view fails twice, then view + scan succeed
got = warehouse.query_parts("t2", "SELECT count(*) c FROM t")
ok("rows came back after retry (got %r)" % got, got == [{"c": 42}])
ok("it actually retried (execute called %d times)" % con.calls, con.calls >= 3)

print("\n3. transient every time → RAISES (loud, so the caller can degrade)")
run([TRANSIENT] * 10)
try:
    warehouse.query_parts("t3", "SELECT 1")
    ok("should have raised", False)
except Exception as e:
    ok("raised instead of returning [] (%s…)" % str(e)[:34], "Failed to read connection" in str(e))

print("\n4. a genuine SQL bug → RAISES, never a silent zero")
run([REAL_BUG])
try:
    warehouse.query_parts("t4", "SELECT nope FROM t")
    ok("should have raised", False)
except Exception as e:
    ok("raised (%s…)" % str(e)[:30], "Binder Error" in str(e))

print("\n5. the SCAN is covered too, not just the view creation")
con = run([None, TRANSIENT, None])                 # view ok, scan blips once, then ok
got = warehouse.query_parts("t5", "SELECT count(*) c FROM t")
ok("scan retried and returned rows (got %r)" % got, got == [{"c": 42}])

print("\n6. a blipped LISTING is retried, and never becomes a false zero")


class FlakyList:
    def __init__(self, fails):
        self.fails, self.calls = fails, 0

    def __call__(self, name):
        self.calls += 1
        if self.calls <= self.fails:
            raise Exception(TRANSIENT)
        return ["p1.parquet"]


con = run([None, None])
fl = FlakyList(2)
warehouse._partition_files_strict = fl
got = warehouse.query_parts("t6", "SELECT count(*) c FROM t")
ok("listing retried then returned rows (got %r)" % got, got == [{"c": 42}])
ok("it actually retried the listing (%d calls)" % fl.calls, fl.calls >= 3)

run([None, None])
warehouse._partition_files_strict = FlakyList(99)
try:
    warehouse.query_parts("t7", "SELECT 1")
    ok("a permanently failing listing should RAISE, not return []", False)
except Exception as e:
    ok("raised instead of a false zero (%s…)" % str(e)[:34], "Failed to read connection" in str(e))

# The whole point of the change: no glob is ever handed to DuckDB.
run([None, None])
warehouse._partition_files_strict = lambda name: ["a.parquet", "b.parquet"]
sqls = []
_orig_con = warehouse.connect


class Recorder(FakeCon):
    def execute(self, sql, params=None):
        sqls.append(sql)
        return super().execute(sql, params)


rec = Recorder([None, None])
warehouse.connect = lambda: rec
warehouse.query_parts("t8", "SELECT 1")
view_sql = next((s for s in sqls if "CREATE OR REPLACE VIEW" in s), "")
ok("the view reads an explicit LIST", "read_parquet([" in view_sql)
ok("the view does NOT glob", "*.parquet" not in view_sql)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
