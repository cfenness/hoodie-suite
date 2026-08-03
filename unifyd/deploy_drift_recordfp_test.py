"""Offline test for deploy_drift record-fp: python deploy_drift_recordfp_test.py

`record-fp` is how the baseline gets written from a machine that HAS warehouse credentials while
still describing the tree we intended to ship. The property that matters is that it records the
fingerprint it is HANDED — not the one it could compute from its own filesystem — because
recording what is live would bless a concurrent session's clobber as the expectation. No network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deploy_drift as dd

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if not cond:
        FAILED.append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — %s" % detail) if not cond else ""))


recorded = {}
dd.record = lambda git_sha, fp=None, root=None, log=print: recorded.update(
    {"git_sha": git_sha, "fp": fp, "root": root}) or True

print("record-fp records the fingerprint it is HANDED")
sha = "a" * 64
rc = dd.main(["record-fp", "deadbeef", sha, "433"])
check("exit 0 on success", rc == 0)
check("git sha is carried through", recorded["git_sha"] == "deadbeef", recorded)
check("the handed fingerprint is what gets recorded", recorded["fp"] == {"sha256": sha, "files": 433},
      recorded)
check("it never fingerprints its own filesystem", recorded["root"] is None, recorded)

print("\nit refuses malformed input rather than recording a wrong baseline")
recorded.clear()
check("missing args -> exit 1", dd.main(["record-fp", "deadbeef", sha]) == 1)
check("…and nothing recorded", recorded == {}, recorded)
recorded.clear()
check("non-integer file count -> exit 1", dd.main(["record-fp", "deadbeef", sha, "many"]) == 1)
check("…and nothing recorded", recorded == {}, recorded)

print("\nrelease_train parses the fingerprint CLI's real output")
import re
import subprocess
out = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   "deploy_drift.py"), "fingerprint", "."],
                     capture_output=True, text=True).stdout.strip()
m = re.match(r"([0-9a-f]{64})\s+\((\d+) files\)", out)
check("the regex release_train uses matches the CLI it parses", bool(m), out[:80])

print("\nthe caller pins the record to the machine it just deployed")
_rt = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "release_train.py")).read()
_call = _rt[_rt.index("record-fp %s %s %s") - 400:_rt.index("record-fp %s %s %s") + 200]
check("the ssh fallback pins -g app", '"-g", "app"' in _call, _call[-200:])
# Why this matters: without -g, flyctl picks ANY machine in the app — including a long-lived
# ephemeral scrape machine on a pre-deploy image. An older deploy_drift.py does not ERROR on the
# unknown `record-fp` subcommand; main() falls through to `check`. So the baseline goes unrecorded
# while the deploy log shows a drift check's output, which reads like it worked. Seen live on v743.
check("exit 0 alone is not accepted as proof the record landed",
      'recorded expected build' in _rt and 'rc2 = 1' in _rt, "missing the confirmation-line guard")

print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
sys.exit(1 if FAILED else 0)
