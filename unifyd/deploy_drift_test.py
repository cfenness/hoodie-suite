"""Offline test for the deploy-drift check: python unifyd/deploy_drift_test.py

The point of this check is to notice what `flyctl releases` cannot: production silently ceasing to
be the build we deployed. So the assertions that matter are the ones about NOT being able to tell —
an unreachable app and a missing baseline must both REPORT, never return "no drift". Two guards
written earlier today were silently inert precisely because they conflated those.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deploy_drift as dd

passed = failed = 0
def ok(n, c):
    global passed, failed
    if c: passed += 1; print("  ok   %s" % n)
    else: failed += 1; print("  FAIL %s" % n)
def eq(n, got, want): ok("%s (got %r)" % (n, got), got == want)

print("fingerprint")
root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, "unifyd")); os.makedirs(os.path.join(root, "apps"))
open(os.path.join(root, "unifyd", "a.py"), "w").write("print(1)\n")
open(os.path.join(root, "apps", "b.html"), "w").write("<p>hi</p>\n")
fp1 = dd.fingerprint(root)
eq("counts the code files", fp1["files"], 2)
ok("is a sha256", len(fp1["sha256"]) == 64)
eq("stable across calls", dd.fingerprint(root)["sha256"], fp1["sha256"])

open(os.path.join(root, "unifyd", "a.py"), "w").write("print(2)\n")
ok("changes when a file changes", dd.fingerprint(root)["sha256"] != fp1["sha256"])
open(os.path.join(root, "unifyd", "a.py"), "w").write("print(1)\n")
eq("and returns when reverted", dd.fingerprint(root)["sha256"], fp1["sha256"])

os.remove(os.path.join(root, "apps", "b.html"))
gone = dd.fingerprint(root)
ok("changes when a file DISAPPEARS — the v623->v627 case", gone["sha256"] != fp1["sha256"])
eq("...and the file count drops", gone["files"], 1)

print("\nruntime artefacts must NOT move the fingerprint (or it becomes noise)")
open(os.path.join(root, "apps", "b.html"), "w").write("<p>hi</p>\n")
base = dd.fingerprint(root)["sha256"]
open(os.path.join(root, "unifyd", "binnys_snapshot.json"), "w").write('{"huge": true}')
eq("a scraper snapshot is ignored", dd.fingerprint(root)["sha256"], base)
os.makedirs(os.path.join(root, "unifyd", "__pycache__"))
open(os.path.join(root, "unifyd", "__pycache__", "x.py"), "w").write("cached")
eq("__pycache__ is ignored", dd.fingerprint(root)["sha256"], base)
os.makedirs(os.path.join(root, "unifyd", "agent_state"))
open(os.path.join(root, "unifyd", "agent_state", "s.json"), "w").write("{}")
eq("agent_state is ignored", dd.fingerprint(root)["sha256"], base)

print("\ncheck() — the verdicts")
EXP = {"git_sha": "abc1234def", "fingerprint": "f" * 64, "files": 305, "recorded_at": 0}
eq("live matches expected -> no findings",
   dd.check(live={"fingerprint": "f" * 64, "files": 305}, exp=EXP), [])
d = dd.check(live={"fingerprint": "e" * 64, "files": 305}, exp=EXP)
eq("a different fingerprint is CRITICAL", d[0][1], "critical")
eq("...with the right code", d[0][0], "deploy-drift")
ok("...and says releases will still look fine", "complete" in d[0][3])

d = dd.check(live={"fingerprint": "e" * 64, "files": 12}, exp=EXP)
ok("missing files are called out as a stale-tree deploy (%s)" % d[0][2][:52],
   "fewer code files" in d[0][2])

print("\n  THE LOAD-BEARING PART: 'cannot tell' must never look like 'no drift'")
d = dd.check(live=None, exp=EXP, fetch=lambda: (_ for _ in ()).throw(OSError("connection refused")))
ok("an unreachable app REPORTS rather than passing", d != [])
eq("...as drift-unreachable", d[0][0], "drift-unreachable")
ok("...and says drift is UNKNOWN, not absent", "UNKNOWN" in d[0][2])

d = dd.check(live={"fingerprint": "f" * 64}, exp=None)
ok("no recorded baseline REPORTS rather than passing", d != [])
eq("...as drift-no-baseline", d[0][0], "drift-no-baseline")
ok("...and says how to set it", "release_train" in d[0][3])

d = dd.check(live={"files": 3}, exp=EXP)
eq("a live payload with no fingerprint REPORTS", d[0][0], "drift-unreachable")

print("\nhealth_digest shape")
f = dd.findings(log=lambda *_a, **_k: None)
ok("findings() returns digest-shaped dicts", all(
    set(x) >= {"code", "severity", "source", "summary", "evidence", "kind"} for x in f))

shutil.rmtree(root, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
