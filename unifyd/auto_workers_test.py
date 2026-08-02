#!/usr/bin/env python3
"""auto_workers_test.py — worker sizing is a MEASURED number now; keep it that way.

Sizing was wrong three times in one day by guesswork (see pace.py's header), and on 2026-08-02 the
utilization meter finally showed why the last formula was wrong in a new direction: it sized off exit
IPs, which answers how much concurrency the addresses tolerate — not how much it takes to spend the
rate budget. Five shards pinned at 6 workers achieved 1.91-2.93 req/s against a 5.0 ceiling.

    python3 auto_workers_test.py
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fails = []


def check(name, got, want=True):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r want %r" % (name, got, want))
        fails.append(name)


def workers(ips, nshard, **env):
    os.environ.pop("UE_WORKERS", None)
    os.environ["ISP_PROXIES"] = ",".join("1.2.3.%d:8080:u:p" % i for i in range(ips))
    for k, v in env.items():
        os.environ[k] = str(v)
    import ue_catalog
    importlib.reload(ue_catalog)
    try:
        return ue_catalog.auto_workers(nshard, log=lambda *a: None)
    finally:
        for k in env:
            os.environ.pop(k, None)


print("sized by the RATE budget, not the IP count")
# 8 shards of a 40/s fleet = 5 req/s each; at ~2.4s occupancy that needs ~15 concurrent, NOT the 37
# the old pool-only formula produced (50 IPs x 6/IP / 8). 37 against a 5/s ceiling is ~22 threads
# parked in acquire() buying nothing.
w = workers(50, 8)
check("today's fleet sizes to the budget, not the pool", 10 <= w <= 20, True)
check("...and is well below the old pool-only answer of 37", w < 37, True)

print("the exit pool is a CEILING that still wins when it is lower than the need")
# 1 IP across 8 shards truncates the allowance to 0. That must not read as "no ceiling" — handing a
# lone address a full Little's-Law worker count is the concentration failure this project measured
# (10 workers on 1 IP: 20% usable -> 0% within three minutes).
check("a degenerate pool falls back to the floor, not to the need", workers(1, 8), 4)
check("a small pool stays at the floor", workers(6, 8), 4)

print("it tracks the inputs it claims to depend on")
base = workers(50, 8)
check("slower per-request service time demands more workers", workers(50, 8, UE_SERVICE_S=6.0) > base, True)
check("faster service time demands fewer", workers(50, 8, UE_SERVICE_S=0.5) < base, True)
check("more shards means a smaller slice of the fleet rate each",
      workers(50, 16) <= base, True)

print("an explicit override still wins — the operator can always pin it")
os.environ["ISP_PROXIES"] = "1.2.3.4:8080:u:p"
os.environ["UE_WORKERS"] = "9"
import ue_catalog
importlib.reload(ue_catalog)
check("UE_WORKERS is honored verbatim", ue_catalog.auto_workers(8, log=lambda *a: None), 9)
os.environ.pop("UE_WORKERS", None)

if fails:
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- auto_workers: concurrency sized to spend the rate budget, capped by what the exits bear")
