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

print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
sys.exit(1 if FAILED else 0)
