"""Offline test for vtinfo.py's registry entrypoint: python unifyd/vtinfo_test.py

Locks in the real bug found live in the health digest, every run since 2026-07-31:

    vtinfo: last run failed — File "/app/unifyd/vtinfo.py", line 257, in <lambda>
            AttributeError: 'list' object has no attribute 'get'

Root cause: `_rows_full` is the codebase-wide convention (ab_locator/chicago/tx_tabc/census/…) —
a list of ROW ARRAYS positionally matching `header`, never a list of dicts (server.py's own
generic full-row export depends on that shape). `run()` fed those bare lists straight into
`warehouse.write_accumulate(..., key=lambda r: r.get(...))`, which needs dicts. No real
warehouse/network call here — pull() and warehouse.write_accumulate are both injected.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vtinfo

passed = failed = 0
def ok(n, c):
    global passed, failed
    if c: passed += 1; print("  ok   %s" % n)
    else: failed += 1; print("  FAIL %s" % n)
def eq(n, got, want): ok("%s (got %r)" % (n, got), got == want)


def _row(brand="titos", account="Joe's Liquors", zip_searched="33601"):
    """A row array in the exact _rows_full shape pull() produces — positionally matching
    vtinfo._LAND_FIELDS / vtinfo.HEADER."""
    return [brand, account, "1 Main St", "Tampa", "FL", "33601", "555-1234", "0.4",
            "27.9", "-82.4", "Liquor Store", "sales", zip_searched]


saved_pull = vtinfo.pull


def _fake_pull(brand="titos", zips=None, log=print, **kw):
    ds = {"vtinfo_%s" % brand: {"header": vtinfo.HEADER, "rows": [_row(brand=brand)],
                                "total": 1, "_rows_full": [_row(brand=brand)]}}
    return ds, [{"connId": "vtinfo"}], {"sampled": 1, "changed": 1, "dropped": 0}


print("run() reconstructs dicts from the row-array _rows_full — this is the live regression")
captured = {}
fake_wh = types.SimpleNamespace(
    write_accumulate=lambda name, records, key, fields=None, **kw: captured.update(
        name=name, records=records, key=key, fields=fields))
saved_wh_mod = sys.modules.get("warehouse")
sys.modules["warehouse"] = fake_wh
vtinfo.pull = _fake_pull
try:
    n = vtinfo.run(brands=("titos",))
finally:
    vtinfo.pull = saved_pull
    if saved_wh_mod is not None: sys.modules["warehouse"] = saved_wh_mod
    else: sys.modules.pop("warehouse", None)

eq("landed 1 row", n, 1)
recs = captured.get("records") or []
eq("one record reached write_accumulate", len(recs), 1)
ok("the record is a DICT, not the raw list (got %r)" % (recs[0] if recs else None), isinstance(recs[0], dict))
eq("brand survived the reconstruction", recs[0].get("brand"), "titos")
eq("account survived the reconstruction", recs[0].get("account"), "Joe's Liquors")
eq("zip_searched survived the reconstruction (this is the accumulate KEY field)",
   recs[0].get("zip_searched"), "33601")

print("\nthe key lambda itself must not raise on the reconstructed record — the literal crash site")
try:
    k = captured["key"](recs[0])
    ok("key(record) succeeds", True)
    eq("...and returns the right identity tuple", k, ("titos", "Joe's Liquors", "33601"))
except AttributeError as e:
    ok("key(record) succeeds (got %r)" % e, False)

print("\nfields passed through unchanged (write_accumulate still projects onto _LAND_FIELDS)")
eq("fields is _LAND_FIELDS", captured.get("fields"), vtinfo._LAND_FIELDS)

print("\nno rows -> write_accumulate is never called (nothing to land, nothing to crash on)")
captured2 = {"called": False}
fake_wh2 = types.SimpleNamespace(
    write_accumulate=lambda *a, **kw: captured2.update(called=True))
sys.modules["warehouse"] = fake_wh2
vtinfo.pull = lambda brand="titos", zips=None, log=print, **kw: (
    {"vtinfo_%s" % brand: {"header": vtinfo.HEADER, "rows": [], "total": 0, "_rows_full": []}},
    [{"connId": "vtinfo"}], {"sampled": 0, "changed": 0, "dropped": 0})
try:
    n2 = vtinfo.run(brands=("titos",))
finally:
    vtinfo.pull = saved_pull
    if saved_wh_mod is not None: sys.modules["warehouse"] = saved_wh_mod
    else: sys.modules.pop("warehouse", None)
eq("zero rows landed", n2, 0)
ok("write_accumulate was never called", not captured2["called"])

print("\na pull() failure for one brand is caught and logged, not raised — other brands still land")
def _mixed_pull(brand="titos", zips=None, log=print, **kw):
    if brand == "broken":
        raise RuntimeError("zip search blew up")
    return _fake_pull(brand=brand, zips=zips, log=log)
captured3 = {}
fake_wh3 = types.SimpleNamespace(
    write_accumulate=lambda name, records, key, fields=None, **kw: captured3.update(records=records))
sys.modules["warehouse"] = fake_wh3
vtinfo.pull = _mixed_pull
msgs = []
try:
    n3 = vtinfo.run(brands=("broken", "titos"), log=msgs.append)
finally:
    vtinfo.pull = saved_pull
    if saved_wh_mod is not None: sys.modules["warehouse"] = saved_wh_mod
    else: sys.modules.pop("warehouse", None)
eq("the healthy brand still lands", n3, 1)
ok("the broken brand's failure was logged, not raised", any("blew up" in m for m in msgs))

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
