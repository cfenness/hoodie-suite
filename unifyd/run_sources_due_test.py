"""Offline tests for the --due dispatcher: python run_sources_due_test.py
due_sources() against a fabricated source_runs ledger + the overlap lock. Local mode, no network."""
import os
import shutil
import sys
import tempfile
import time

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import run_sources
import source_registry as reg

TMP = tempfile.mkdtemp(prefix="wh_due_")
warehouse._LOCAL_DIR = TMP

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


NOW = time.time()
enabled = [s for s in reg.SOURCES if s.get("enabled")]

# 1) no ledger yet -> every enabled source is due
ok("all enabled due with empty ledger", {s["id"] for s in run_sources.due_sources(now=NOW)} == {s["id"] for s in enabled})

# 2) fabricate a ledger: binnys ran 1h ago (fresh), specs 25h ago (due), ttb-style weekly fresh at 100h
recs = [
    dict(run_id="binnys-x", source="binnys", ts_start=NOW - 3600),
    dict(run_id="specs-x", source="specs", ts_start=NOW - 25 * 3600),
    dict(run_id="winebow-x", source="winebow", ts_start=NOW - 100 * 3600),   # weekly: due at 168h
]
warehouse.write_parquet("source_runs", recs, fields=["run_id", "source", "ts_start"])
due = {s["id"] for s in run_sources.due_sources(now=NOW)}
ok("fresh daily skipped", "binnys" not in due)
ok("stale daily due", "specs" in due)
ok("fresh weekly skipped", "winebow" not in due)
ok("never-run still due", "target" in due)

# 3) interval_h override: give binnys a hot 30-minute interval -> due despite 1h-old run
b = reg.by_id("binnys")
b["interval_h"] = 0.5
ok("interval_h override makes hot source due", "binnys" in {s["id"] for s in run_sources.due_sources(now=NOW)})
del b["interval_h"]

# 4) grace: a 24h source last run 23.9h ago is due (0.98 grace), 20h ago is not
warehouse.write_parquet("source_runs", [dict(run_id="b1", source="binnys", ts_start=NOW - 23.9 * 3600)],
                        fields=["run_id", "source", "ts_start"], allow_empty=True)
ok("grace catches near-interval", "binnys" in {s["id"] for s in run_sources.due_sources(now=NOW)})
warehouse.write_parquet("source_runs", [dict(run_id="b2", source="binnys", ts_start=NOW - 20 * 3600)],
                        fields=["run_id", "source", "ts_start"], allow_empty=True)
ok("well-inside interval not due", "binnys" not in {s["id"] for s in run_sources.due_sources(now=NOW)})

# 5) the overlap lock: second acquire in-process fails while the first is held
l1 = run_sources._acquire_lock()
ok("lock acquired", l1 is not None)
ok("second acquire refused", run_sources._acquire_lock() is None)
l1.close()
ok("released lock re-acquirable", run_sources._acquire_lock() is not None)

# 6) derived builds (Phase 3): trigger on fresh 'ok' landings, throttled by min gap
SR = ["run_id", "source", "ts_start", "ts_end", "status"]


def ledger(rows):
    warehouse.write_parquet("source_runs", rows, fields=SR, allow_empty=True)


ledger([])
ok("no landings -> no builds due", run_sources.due_builds(now=NOW) == [])

# a source landed new rows 1h ago; builds never ran -> both due
ledger([dict(run_id="s1", source="binnys", ts_start=NOW - 3700, ts_end=NOW - 3600, status="ok")])
ok("fresh ok landing -> builds due",
   {b["id"] for b in run_sources.due_builds(now=NOW)} == {"build-outlets", "build-product-master"})

# build ran AFTER the landing -> not due; and a 'current' (no new rows) landing never triggers
ledger([dict(run_id="s1", source="binnys", ts_start=NOW - 3700, ts_end=NOW - 3600, status="ok"),
        dict(run_id="b1", source="build-outlets", ts_start=NOW - 3000, ts_end=NOW - 2900, status="ok"),
        dict(run_id="b2", source="build-product-master", ts_start=NOW - 3000, ts_end=NOW - 2900, status="ok"),
        dict(run_id="s2", source="specs", ts_start=NOW - 1000, ts_end=NOW - 900, status="current")])
ok("built after landing + only 'current' since -> not due", run_sources.due_builds(now=NOW) == [])

# new ok landing since the build, but build ran 1h ago (< 6h min gap) -> throttled
ledger([dict(run_id="b1", source="build-outlets", ts_start=NOW - 3600, ts_end=NOW - 3500, status="ok"),
        dict(run_id="b2", source="build-product-master", ts_start=NOW - 3600, ts_end=NOW - 3500, status="ok"),
        dict(run_id="s3", source="specs", ts_start=NOW - 600, ts_end=NOW - 500, status="ok")])
ok("min gap throttles rebuild", run_sources.due_builds(now=NOW) == [])

# build ran 7h ago (> 6h gap for build-outlets, < 12h for product-master), landing 1h ago
ledger([dict(run_id="b1", source="build-outlets", ts_start=NOW - 7 * 3600, ts_end=NOW - 7 * 3600 + 60, status="ok"),
        dict(run_id="b2", source="build-product-master", ts_start=NOW - 7 * 3600, ts_end=NOW - 7 * 3600 + 60, status="ok"),
        dict(run_id="s4", source="specs", ts_start=NOW - 3600, ts_end=NOW - 3500, status="ok")])
ok("per-build min gaps respected",
   {b["id"] for b in run_sources.due_builds(now=NOW)} == {"build-outlets"})

# after=[...] narrows the trigger
b_out = next(b for b in reg.BUILDS if b["id"] == "build-outlets")
b_out["after"] = ["ca-abc"]
ok("after-list ignores unrelated landings", run_sources.due_builds(now=NOW) == [])
del b_out["after"]

# 7) mac quiet-hours window (default 20-8, env-tunable, wrap + non-wrap)
def at_hour(h):
    t = list(time.localtime())
    t[3], t[4], t[5] = h, 0, 0
    return time.mktime(tuple(t))


os.environ.pop("MAC_HOURS", None)
ok("default window open at 23:00", run_sources.mac_window_open(at_hour(23)))
ok("default window open at 03:00", run_sources.mac_window_open(at_hour(3)))
ok("default window closed at 14:00", not run_sources.mac_window_open(at_hour(14)))
os.environ["MAC_HOURS"] = "9-17"
ok("non-wrapping window honored", run_sources.mac_window_open(at_hour(10))
   and not run_sources.mac_window_open(at_hour(20)))
os.environ["MAC_HOURS"] = "garbage"
ok("bad spec falls back to default", not run_sources.mac_window_open(at_hour(14)))
os.environ.pop("MAC_HOURS", None)

# 8) mac ordering: priorities put aggregators first, anti-bot trio last
mac = sorted([s for s in enabled if s["klass"] == "mac"], key=lambda s: s.get("priority", 50))
ids = [s["id"] for s in mac]
ok("aggregators first", ids[:2] == ["ubereats", "postmates"])
ok("anti-bot trio last", ids[-3:] == ["sevennow", "cityhive", "bottlecapps"])

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
