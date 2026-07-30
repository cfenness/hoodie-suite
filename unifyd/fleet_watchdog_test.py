#!/usr/bin/env python3
"""fleet_watchdog_test.py — the cross-sectional collision check against synthetic shard logs, pure
logic, no network, no real Fly log files needed.

    python3 fleet_watchdog_test.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fleet_watchdog as W

fails = []


def check(name, got, want=True):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r want %r" % (name, got, want))
        fails.append(name)


def write_log(path, *beats):
    with open(path, "w") as f:
        for hb in beats:
            f.write("some other line, not a heartbeat\n")
            f.write(W.PROGRESS_PREFIX + json.dumps(hb) + "\n")


tmp = tempfile.mkdtemp(prefix="fleet_watchdog_test_")
try:
    print("latest_heartbeat() reads the LAST beat in a file, ignoring non-progress lines")
    p = os.path.join(tmp, "s0.log")
    write_log(p, {"hot_exits": ["1.1.1.1"]}, {"hot_exits": ["2.2.2.2"]})
    hb = W.latest_heartbeat(p)
    check("picked the second (latest) beat", hb.get("hot_exits"), ["2.2.2.2"])

    print("a missing/empty file returns None, not an error")
    check("nonexistent path", W.latest_heartbeat(os.path.join(tmp, "nope.log")), None)

    print("no overlap across shards -> no collision")
    p0 = os.path.join(tmp, "clean0.log")
    p1 = os.path.join(tmp, "clean1.log")
    write_log(p0, {"hot_exits": ["10.0.0.1", "10.0.0.2"]})
    write_log(p1, {"hot_exits": ["10.0.0.3", "10.0.0.4"]})
    result = W.scan([p0, p1])
    check("both shards reported", sum(1 for v in result["shards"].values() if v), 2)
    check("no collisions when hot sets are disjoint", result["collisions"], [])

    print("two shards naming the same exit right now IS a collision")
    p2 = os.path.join(tmp, "collide0.log")
    p3 = os.path.join(tmp, "collide1.log")
    write_log(p2, {"hot_exits": ["10.0.0.9", "10.0.0.5"]})
    write_log(p3, {"hot_exits": ["10.0.0.9", "10.0.0.6"]})
    result = W.scan([p2, p3])
    check("exactly one collision found", len(result["collisions"]), 1)
    check("the shared exit is the flagged one", result["collisions"][0]["exit"], "10.0.0.9")
    check("both shards named in the collision",
          sorted(result["collisions"][0]["shards"]), sorted([p2, p3]))

    print("three-way overlap reports all three shards, not just two")
    p4 = os.path.join(tmp, "three0.log")
    p5 = os.path.join(tmp, "three1.log")
    p6 = os.path.join(tmp, "three2.log")
    write_log(p4, {"hot_exits": ["10.0.0.7"]})
    write_log(p5, {"hot_exits": ["10.0.0.7"]})
    write_log(p6, {"hot_exits": ["10.0.0.7"]})
    result = W.scan([p4, p5, p6])
    check("one collision entry covering all three", len(result["collisions"]), 1)
    check("all three shards named", len(result["collisions"][0]["shards"]), 3)

    print("a shard with no heartbeat yet doesn't crash the scan or count as a collision partner")
    p7 = os.path.join(tmp, "silent.log")
    open(p7, "w").close()               # exists, empty — no heartbeat written yet
    result = W.scan([p0, p1, p7])
    check("silent shard reports None", result["shards"][p7], None)
    check("silent shard contributes no collisions", result["collisions"], [])

    print("a truncated/mid-write JSON line is skipped, not fatal")
    p8 = os.path.join(tmp, "truncated.log")
    with open(p8, "w") as f:
        f.write(W.PROGRESS_PREFIX + json.dumps({"hot_exits": ["10.0.0.8"]}) + "\n")
        f.write(W.PROGRESS_PREFIX + '{"hot_exits": ["10.0.0.99"'  )  # cut off mid-write, no newline needed
    hb = W.latest_heartbeat(p8)
    check("falls back to the last COMPLETE beat", hb.get("hot_exits"), ["10.0.0.8"])

    print("main() exits 1 when a collision is found, 0 when clean, 2 when nothing matched")
    check("clean pair -> exit 0", W.main([p0, p1]), 0)
    check("colliding pair -> exit 1", W.main([p2, p3]), 1)
    check("no matching files -> exit 2", W.main([os.path.join(tmp, "no-such-*.log")]), 2)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- fleet_watchdog: cross-sectional (not trend-based) cross-shard collision detection")
