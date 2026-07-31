"""Offline test for the warehouse-wide DQ aggregate: python unifyd/dq_aggregate_test.py

Everything is injected (list_datasets_fn / query_fn / deep_audit._null_fracs monkeypatched) so this
runs with no real warehouse, same idiom as deploy_drift_test.py.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dq_aggregate as dqa

passed = failed = 0
def ok(n, c):
    global passed, failed
    if c: passed += 1; print("  ok   %s" % n)
    else: failed += 1; print("  FAIL %s" % n)
def eq(n, got, want): ok("%s (got %r)" % (n, got), got == want)


def _stub_deep_audit(fracs_by_table):
    """Patch deep_audit._null_fracs so _null_blowout doesn't need real Parquet footers."""
    fake = types.SimpleNamespace(_null_fracs=lambda name: fracs_by_table.get(name))
    sys.modules["deep_audit"] = fake


def _restore_deep_audit(saved):
    if saved is not None: sys.modules["deep_audit"] = saved
    else: sys.modules.pop("deep_audit", None)


saved_da = sys.modules.get("deep_audit")

print("group + row-count filtering — only real-data groups, above the noise floor")
datasets = [
    {"name": "dim_brand", "group": "master", "rows": 500},
    {"name": "_scratch_tmp", "group": "staging", "rows": 5000},
    {"name": "master_decisions", "group": "derived", "rows": 5000},
    {"name": "specs_runs", "group": "runlog", "rows": 5000},
    {"name": "tiny_seed", "group": "scrape", "rows": 3},          # below MIN_ROWS
]
_stub_deep_audit({"dim_brand": {}})
d = dqa.build(list_datasets_fn=lambda: datasets, query_fn=lambda n, sql: [{"n": 0}])
eq("only dim_brand qualifies", d["tables_checked"], 1)
_restore_deep_audit(saved_da)

print("\nnull-blowout — a fully-empty column is a critical finding, no baseline needed")
datasets = [{"name": "dim_sku", "group": "master", "rows": 1000}]
_stub_deep_audit({"dim_sku": {"upc": 1.0, "brand": 0.02, "raw_json": 1.0}})
d = dqa.build(list_datasets_fn=lambda: datasets, query_fn=lambda n, sql: [{"n": 0}])
f = [x for x in d["findings"] if x["code"] == "dq-null-blowout"]
eq("one blowout finding", len(f), 1)
ok("names the dead column", "upc" in f[0]["evidence"]["columns"])
ok("raw_json is exempt (payload column, not a normal field)", "raw_json" not in f[0]["evidence"]["columns"])
eq("severity is critical", f[0]["severity"], "critical")
eq("table is not clean", d["tables_clean"], 0)
_restore_deep_audit(saved_da)

print("\nno finding when nothing is fully null")
_stub_deep_audit({"dim_sku": {"upc": 0.01, "brand": 0.0}})
d = dqa.build(list_datasets_fn=lambda: datasets, query_fn=lambda n, sql: [{"n": 0}])
eq("no null-blowout findings", len([x for x in d["findings"] if x["code"] == "dq-null-blowout"]), 0)
eq("table counted clean", d["tables_clean"], 1)
eq("score is 100", d["score"], 100)
_restore_deep_audit(saved_da)

print("\nfooter-unreadable table (partitioned / no stats) is SKIPPED, never counted clean by default")
datasets = [{"name": "fact_velocity", "group": "timeseries", "rows": 50000, "partitioned": True}]
_stub_deep_audit({})
d = dqa.build(list_datasets_fn=lambda: datasets, query_fn=lambda n, sql: [{"n": 0}])
cov = [x for x in d["findings"] if x["code"] == "dq-coverage" and "null-blowout" in x["summary"]]
ok("reports the skip, doesn't stay silent", len(cov) == 1 and "fact_velocity" in cov[0]["evidence"]["skipped"])
eq("still counted clean (no duplicates found either)", d["tables_clean"], 1)
_restore_deep_audit(saved_da)

print("\nduplicate rows — exact whole-row dupes")
datasets = [{"name": "src_outlets", "group": "conformed", "rows": 100}]
_stub_deep_audit({"src_outlets": {}})
calls = []
def q_with_dupes(name, sql):
    calls.append(sql)
    if "DISTINCT" in sql:
        return [{"n": 91}]                              # 9 duplicate rows
    return [{"n": 100}]
d = dqa.build(list_datasets_fn=lambda: datasets, query_fn=q_with_dupes)
f = [x for x in d["findings"] if x["code"] == "dq-duplicate-rows"]
eq("one duplicate finding", len(f), 1)
eq("counts 9 dup rows", f[0]["evidence"]["duplicate_rows"], 9)
eq("9% rate -> critical (>=5%)", f[0]["severity"], "critical")
_restore_deep_audit(saved_da)

print("\nlow-rate duplicates warn, not critical")
def q_low_dupes(name, sql):
    if "DISTINCT" in sql: return [{"n": 99}]
    return [{"n": 100}]
_stub_deep_audit({"src_outlets": {}})
d = dqa.build(list_datasets_fn=lambda: datasets, query_fn=q_low_dupes)
f = [x for x in d["findings"] if x["code"] == "dq-duplicate-rows"]
eq("1% rate -> warn", f[0]["severity"], "warn")
_restore_deep_audit(saved_da)

print("\nlarge tables are SKIPPED for the duplicate scan, never silently full-scanned")
big = [{"name": "retail_observations", "group": "timeseries", "rows": dqa.DUP_SCAN_MAX_ROWS + 1}]
_stub_deep_audit({"retail_observations": {}})
called = []
d = dqa.build(list_datasets_fn=lambda: big, query_fn=lambda n, sql: called.append(sql) or [{"n": 0}])
ok("never ran a duplicate query against it", not any("DISTINCT" in c for c in called))
cov = [x for x in d["findings"] if x["code"] == "dq-coverage" and "duplicate scan" in x["summary"]]
ok("reports the skip with the ceiling", len(cov) == 1 and cov[0]["evidence"]["ceiling_rows"] == dqa.DUP_SCAN_MAX_ROWS)
_restore_deep_audit(saved_da)

print("\nempty warehouse — no candidates, honest score of None (not a fake 100)")
d = dqa.build(list_datasets_fn=lambda: [], query_fn=lambda n, sql: [{"n": 0}])
eq("zero tables checked", d["tables_checked"], 0)
eq("score is None, not a misleading 100", d["score"], None)
ok("still ok (no criticals) with nothing to check", d["ok"] is True)

print("\na query failure is a skip, not a crash")
datasets = [{"name": "flaky_table", "group": "scrape", "rows": 500}]
_stub_deep_audit({"flaky_table": {}})
def q_raises(name, sql):
    raise RuntimeError("connection reset")
d = dqa.build(list_datasets_fn=lambda: datasets, query_fn=q_raises)
ok("build() did not raise", True)
cov = [x for x in d["findings"] if x["code"] == "dq-coverage" and "duplicate scan" in x["summary"]]
ok("the flaky table is reported skipped, not silently dropped", "flaky_table" in cov[0]["evidence"]["skipped"])
_restore_deep_audit(saved_da)

print("\nfindings() wrapper — health_digest-shaped, and survives a hard failure")
def q_ok(name, sql): return [{"n": 0}]
_stub_deep_audit({"dim_sku": {}})
fs = dqa.findings(list_datasets_fn=lambda: [{"name": "dim_sku", "group": "master", "rows": 1000}], query_fn=q_ok,
                  log=lambda *_a, **_k: None)
ok("returns a list", isinstance(fs, list))
_restore_deep_audit(saved_da)

def q_boom(name, sql): raise RuntimeError("boom")
def list_boom(): raise RuntimeError("boom")
msgs = []
fs = dqa.findings(list_datasets_fn=list_boom, log=msgs.append)
eq("a hard failure still returns a findings list", len(fs), 1)
eq("...tagged dq-check-failed", fs[0]["code"], "dq-check-failed")
ok("...and is logged, not silent", len(msgs) == 1)

print("\nwrite() must not publish a score the reader cannot see")
d1 = {"name": "dim_sku", "group": "master", "rows": 1000}
_stub_deep_audit({"dim_sku": {}})
fake_local = types.SimpleNamespace(remote=lambda: False, put_bytes=lambda *a: (_ for _ in ()).throw(
    AssertionError("must not write when the warehouse is local")))
saved_wh = sys.modules.get("warehouse")
sys.modules["warehouse"] = fake_local
msgs = []
r = dqa.write(list_datasets_fn=lambda: [d1], query_fn=lambda n, sql: [{"n": 0}], log=msgs.append)
eq("a local-only warehouse publishes nothing", r, None)
ok("...and says the score stays unreadable", any("NOT published" in m for m in msgs))

wrote = {}
fake_remote = types.SimpleNamespace(remote=lambda: True, put_bytes=lambda k, v: wrote.update({k: v}))
sys.modules["warehouse"] = fake_remote
msgs = []
r = dqa.write(list_datasets_fn=lambda: [d1], query_fn=lambda n, sql: [{"n": 0}], log=msgs.append)
ok("a shared warehouse DOES publish", r is not None and dqa.STATE_KEY in wrote)
ok("...and says so with the score", any("published: score" in m for m in msgs))
if saved_wh is not None: sys.modules["warehouse"] = saved_wh
else: sys.modules.pop("warehouse", None)
_restore_deep_audit(saved_da)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
