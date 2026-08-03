"""Offline test for vtinfo_bbs.targets(): python vtinfo_bbs_targets_test.py

The pull's distributor list is the whole point of the census. This pins the behaviour that was
actually broken in production: `pull()` defaulted to a ONE-entry hand-kept dict while
`vip_brandbuilder_directory` held 338 confirmed catalogs, so the scheduled pass refreshed one
distributor and 337 sat frozen with nothing reporting it. No network, no DuckDB.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vtinfo_bbs

passed = failed = 0


def ok(name, cond, detail=""):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name + (("  — %s" % detail) if not cond and detail else ""))
    passed += bool(cond)
    failed += not cond


class FakeWarehouse(object):
    def __init__(self, rows=None, boom=None):
        self.rows, self.boom, self.sql = rows, boom, None

    def query(self, table, sql=None, params=None):
        self.sql = sql
        if self.boom:
            raise RuntimeError(self.boom)
        return self.rows


def with_warehouse(fake):
    """targets() imports `warehouse` lazily, so swapping the module entry is enough."""
    sys.modules["warehouse"] = fake
    return fake


logs = []
LOG = logs.append

# ── the fix: the directory is the pull list ───────────────────────────────────────────────────
fake = with_warehouse(FakeWarehouse(rows=[
    {"code": "01191", "name": "Columbia Distributing - WA"},
    {"code": "00177", "name": "Florida Distributing Company"},
    {"code": "90901", "name": "Some Big Book"},
]))
logs.clear()
t = vtinfo_bbs.targets(log=LOG)
ok("targets reads the census directory", set(t) == {"01191", "00177", "90901"}, t)
ok("targets is not the hand-kept seed", len(t) > len(vtinfo_bbs.DISTRIBUTORS))
ok("names come along for the run log", t["00177"] == "Florida Distributing Company", t)

# `info_only` codes carry no catalog — pulling them burns three requests to land zero rows
ok("only CONFIRMED distributors are pulled", "status = 'confirmed'" in (fake.sql or ""), fake.sql)

# ── honest degradation: a fallback that is silent is the bug, not the fallback ────────────────
with_warehouse(FakeWarehouse(boom="Tigris read failed"))
logs.clear()
t = vtinfo_bbs.targets(log=LOG)
ok("unreadable directory falls back to the seed", t == dict(vtinfo_bbs.DISTRIBUTORS), t)
ok("…and says the pass covers far less than the full book",
   any("FAR less" in m for m in logs), logs)

with_warehouse(FakeWarehouse(rows=[]))
logs.clear()
t = vtinfo_bbs.targets(log=LOG)
ok("empty directory falls back to the seed", t == dict(vtinfo_bbs.DISTRIBUTORS), t)
ok("…and names the census as the fix", any("vip-brandbuilder-census" in m for m in logs), logs)

# a row with no code must not become an empty sourceCode in the pull list
with_warehouse(FakeWarehouse(rows=[{"code": "", "name": "x"}, {"code": "01191", "name": "y"}]))
ok("blank source codes are dropped", set(vtinfo_bbs.targets(log=LOG)) == {"01191"})

# ── explicit arguments still win, so a one-distributor run stays possible ─────────────────────
ok("pull() still accepts an explicit distributor",
   "dist" in vtinfo_bbs.pull.__code__.co_varnames)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "vtinfo_bbs.py")).read()
ok("pull() defaults to targets(), not the seed dict",
   "or list(targets(log).keys())" in src and "or list(DISTRIBUTORS.keys())" not in src)

print("\n%d checks, %d failed" % (passed + failed, failed))
sys.exit(1 if failed else 0)
