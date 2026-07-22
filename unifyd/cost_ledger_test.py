"""Offline test for the cost ledger: python cost_ledger_test.py
Fabricated ledger, isolated local warehouse, pinned rates. Proves the MEASURED aggregation is real
and the MODELED $ overlay applies cost_class rates + rollups correctly. No network."""
import os
import shutil
import sys
import tempfile
import time

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)
# pin rates so the assertions are deterministic regardless of the shell env
os.environ.update(COST_MAC_USD_PER_HR="0", COST_ACTIONS_USD_PER_HR="0", COST_BD_USD_PER_1K="1.5",
                  COST_PROXY_USD_PER_GB="3.0", COST_WORKER_USD_PER_HR="0")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import source_registry as reg

TMP = tempfile.mkdtemp(prefix="cost_test_")
warehouse._LOCAL_DIR = TMP
import cost_ledger

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


# pick real source ids of each class so cost_class lookups resolve
bd = next(s["id"] for s in reg.SOURCES if s.get("cost_class") == "bd")
proxy = next(s["id"] for s in reg.SOURCES if s.get("cost_class") == "proxy")
free = next(s["id"] for s in reg.SOURCES if not s.get("cost_class"))

NOW = time.time()
SR = ["run_id", "source", "ts_start", "ts_end", "duration_s", "status", "delta"]
recs = [
    dict(run_id="a", source=bd, ts_start=NOW - 3600, ts_end=NOW, duration_s=1800, status="ok", delta=100),
    dict(run_id="b", source=bd, ts_start=NOW - 7200, ts_end=NOW, duration_s=1800, status="ok", delta=0),
    dict(run_id="c", source=proxy, ts_start=NOW - 3600, ts_end=NOW, duration_s=600, status="failed", delta=0),
    dict(run_id="d", source=free, ts_start=NOW - 3600, ts_end=NOW, duration_s=3600, status="ok", delta=50),
    dict(run_id="e", source=free, ts_start=NOW - 40 * 86400, ts_end=NOW, duration_s=999, status="ok", delta=9),  # outside window
]
warehouse.write_partition("source_runs_log", "t1", recs, fields=SR)

rep = cost_ledger.report(window_days=30)
srow = {r["source"]: r for r in rep["sources"]}

# MEASURED: runs + machine-hours + rows added, and the out-of-window row is excluded
ok("measured runs per source", srow[bd]["runs"] == 2 and srow[proxy]["runs"] == 1 and srow[free]["runs"] == 1)
ok("out-of-window run excluded", srow[free]["runs"] == 1 and srow[free]["rows_added"] == 50)
ok("machine-hours summed", abs(srow[bd]["machine_hours"] - 1.0) < 1e-6)   # 2 × 1800s = 1h
ok("rows added counts only positive deltas", srow[bd]["rows_added"] == 100)

# MODELED: bd = 1500 calls/run × 2 runs / 1000 × $1.5 = $4.50 ; proxy = 1.5 GB × 1 × $3 = $4.50 ; free = $0
ok("bd infra $ from per-run calls", abs(srow[bd]["infra_usd_mo"] - 4.5) < 0.01)
ok("proxy infra $ from per-run GB", abs(srow[proxy]["infra_usd_mo"] - 4.5) < 0.01)
ok("free source costs nothing", srow[free]["total_usd_mo"] == 0.0)
ok("compute is $0 on free hosts", srow[bd]["compute_usd_mo"] == 0.0 and srow[free]["compute_usd_mo"] == 0.0)

# ROLLUPS
ok("total = sum of sources", abs(rep["totals"]["total_usd_mo"] - sum(r["total_usd_mo"] for r in rep["sources"])) < 0.02)
ok("by_class present for bd + proxy", rep["by_class"].get("bd", 0) > 0 and rep["by_class"].get("proxy", 0) > 0)
ok("cost concentrates in paid classes", rep["by_class"].get("free", 0) == 0.0)

# a source failing but still costing infra is visible (paid-to-land-nothing signal)
ok("failed proxy run still costs", srow[proxy]["infra_usd_mo"] > 0 and srow[proxy]["rows_added"] == 0)

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
