"""Offline test: every poller's mirror key is a REGISTERED run-doc id.

A poller writes `_scrape_progress/<SID>.md`; the tracker reads `_scrape_progress/<id>.md` for the
id declared in server.py's _RUN_DOCS. Nothing enforces that those two strings agree, and when they
silently disagreed the failure was invisible: the DoorDash regional poller derived "doordash" from
its filename and mirrored regional ticks over the FULL-catalog run's key, so the tracker showed one
run's numbers under the other's label — no error, no empty state (#776, #779).

This is the check that would have caught it. No network, no imports of Flask.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if not cond:
        FAILED.append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — %s" % detail) if not cond else ""))


server = open(os.path.join(ROOT, "unifyd", "server.py")).read()
m = re.search(r"_RUN_DOCS = \[(.*?)\n\]", server, re.S)
IDS = set(re.findall(r'"id":\s*"([^"]+)"', m.group(1)))
print("registered run-doc ids: %s" % sorted(IDS))

# TRACKED files only. Enumerating the directory picked up local strays — an iCloud
# "poll_ue_progress 2.py" duplicate carrying the pre-fix code failed this test on the operator's
# Mac while a clean checkout passed. A guard whose result depends on someone's untracked litter is
# a guard people learn to ignore. Strays are still surfaced, as a warning, below.
import subprocess
_tracked = subprocess.run(["git", "-C", ROOT, "ls-files", "tools/"],
                          capture_output=True, text=True).stdout.split()
POLLERS = sorted(os.path.basename(f) for f in _tracked
                 if re.fullmatch(r"poll_.*\.py", os.path.basename(f)))
check("the pollers are discovered", len(POLLERS) >= 2, POLLERS)

_ondisk = {f for f in os.listdir(os.path.join(ROOT, "tools")) if re.fullmatch(r"poll_.*\.py", f)}
_strays = sorted(_ondisk - set(POLLERS))
if _strays:
    print("  note: untracked poll_*.py present, NOT tested (iCloud duplicates?): %s"
          % ", ".join(_strays))

for f in POLLERS:
    src = open(os.path.join(ROOT, "tools", f)).read()
    m = re.search(r'^SID = .*?else "([^"]+)"', src, re.M)
    check("%s declares a literal default SID" % f, bool(m),
          "SID is still derived — a filename is a guess, an id is a fact")
    if m:
        check("%s SID %r is a registered run-doc id" % (f, m.group(1)), m.group(1) in IDS,
              "not in %s" % sorted(IDS))
    # the derivation that caused the bug must not come back
    check("%s does not derive SID from the filename" % f,
          'basename(DOC).split("-")[0]' not in src, "the first-dash rule is back")
    # and the key it writes must be built from SID, not from something else
    check("%s mirrors to _scrape_progress/<SID>" % f,
          '"_scrape_progress/%s.md" % SID' in src, "mirror key is not SID-derived")

# Informational, NOT an assertion: a finished run keeps its doc and its warehouse mirror after its
# poller is gone (doordash's full-catalog sweep is exactly that — 338KB still served, no poller in
# the repo). Requiring one poller per registered id would fail for a correct, completed run, so
# this reports the gap instead of failing on it.
fed = set()
for f in POLLERS:
    mm = re.search(r'^SID = .*?else "([^"]+)"', open(os.path.join(ROOT, "tools", f)).read(), re.M)
    if mm:
        fed.add(mm.group(1))
unfed = sorted(IDS - fed)
print("\nrun docs with no poller in the repo (finished runs, or a Mac-side script never committed):")
print("  %s" % (", ".join(unfed) if unfed else "none"))

print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
sys.exit(1 if FAILED else 0)
