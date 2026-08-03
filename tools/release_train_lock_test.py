"""Offline test for the release-train lock: python tools/release_train_lock_test.py

A lock is stale when its HOLDER IS GONE, not merely when it is old. Expiring on age alone wedged
the repo for a full hour after a crashed deploy — observed live: pid 6559 had been dead for 19
minutes and every deploy still refused, which reads as "someone else is shipping" when nobody was.

The risk in the other direction is far worse (two concurrent deploys), so every uncertain case must
keep the old age rule. That asymmetry is what this pins. No network.
"""
import json
import os
import socket
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_train as rt

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if not cond:
        FAILED.append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — %s" % detail) if not cond else ""))


HOST = socket.gethostname()
TMP = tempfile.mkdtemp(prefix="rtlock_")
rt.lock_path = lambda: os.path.join(TMP, "release-train.lock")


def write_lock(**kw):
    d = {"pid": os.getpid(), "what": "deploy", "at": time.time(),
         "when": "now", "host": HOST}
    d.update(kw)
    with open(rt.lock_path(), "w") as f:
        json.dump(d, f)


def dead_pid():
    """A pid that provably does not exist: fork a child, reap it, reuse its pid immediately."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


print("a live holder still blocks")
write_lock(pid=os.getpid())
got, why = rt.acquire_lock("deploy")
check("a lock held by a LIVE pid is not reclaimed", got is False, why)
check("…and it says who holds it", "held by pid" in (why or ""), why)

print("\na dead holder is reclaimed immediately, not after an hour")
d = dead_pid()
write_lock(pid=d, at=time.time())          # brand new lock, but its process is gone
got, why = rt.acquire_lock("deploy")
check("a lock whose pid is gone is reclaimed at once", got is True, why)
rt.release_lock()

print("\nevery uncertain case falls back to the age rule (never reclaim on a guess)")
check("a foreign host's pid is never judged",
      rt._holder_dead({"pid": d, "host": "some-other-machine"}) is False)
check("a lock with no host (pre-dates the field) is never judged",
      rt._holder_dead({"pid": d}) is False)
check("a missing pid is never judged", rt._holder_dead({"host": HOST}) is False)
check("a non-integer pid is never judged", rt._holder_dead({"pid": "abc", "host": HOST}) is False)
check("our OWN pid is never judged dead", rt._holder_dead({"pid": os.getpid(), "host": HOST}) is False)
check("a dead pid on THIS host is judged dead", rt._holder_dead({"pid": d, "host": HOST}) is True)

print("\nage still expires a lock even when liveness cannot be judged")
write_lock(pid=os.getpid(), host="some-other-machine", at=time.time() - (rt.LOCK_STALE_SEC + 60))
got, why = rt.acquire_lock("deploy")
check("an old foreign lock still expires on age", got is True, why)
rt.release_lock()

print("\nthe lock it writes records the host, so the next run can trust the pid")
rt.acquire_lock("deploy")
held = json.load(open(rt.lock_path()))
check("host is recorded", held.get("host") == HOST, held)
check("pid is recorded", held.get("pid") == os.getpid(), held)
rt.release_lock()
check("release removes the lock", not os.path.exists(rt.lock_path()))

print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
sys.exit(1 if FAILED else 0)
