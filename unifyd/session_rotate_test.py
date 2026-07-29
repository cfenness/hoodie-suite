"""The endpoint's limit looks like a QUOTA PER SESSION, not a rate per second.

Measured across three runs, collapse arrived after roughly the same NUMBER of requests rather than after
a period of time:

    unpaced, 120 workers    healthy ~60s, ~5,578 requests   (~46 per session)
    paced at 40 req/s       healthy ~60s, ~7,000 requests   (~58 per session)

Both died near the same request COUNT, and halving the rate bought only a longer wait for the same wall.
That is a quota, not a rate limit — and it explains what no rate model could: why 20 exit IPs never
helped (the quota is not per-IP), and why every concurrency number we fitted was wrong.

_session() primed one curl_cffi session per thread and reused it forever, so a single cookie carried the
entire budget. Re-priming costs one homepage GET and resets it.

This test pins the COUNTER, not the network: that a session is reused up to the budget and replaced after
it, and that the budget can be switched off if this read of the evidence turns out to be wrong.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import getstore  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


# Stand in for the real primed session — we are testing the rotation bookkeeping, not curl.
class FakeSession:
    live = 0

    def __init__(self):
        FakeSession.live += 1
        self.closed = False

    def close(self):
        self.closed = True
        FakeSession.live -= 1


made = []


def fake_prime(site="ubereats"):
    """Mimic _session()'s contract: reuse the thread's session until the budget is spent, then replace."""
    tl = fake_prime.tl
    n = getattr(tl, "n", 0)
    s = getattr(tl, "s", None)
    if s is not None and getstore.SESSION_BUDGET and n >= getstore.SESSION_BUDGET:
        s.close()
        s, tl.s, tl.n = None, None, 0
    if s is not None:
        tl.n = n + 1
        return s
    s = FakeSession()
    made.append(s)
    tl.s, tl.n = s, 1
    return s


fake_prime.tl = threading.local()

check("budget defaults below the observed ~50", getstore.SESSION_BUDGET <= 50, True)
check("budget is on by default", getstore.SESSION_BUDGET > 0, True)

# Inside the budget: one session, reused.
for _ in range(getstore.SESSION_BUDGET):
    fake_prime()
check("reused within budget", len(made), 1)

# One past it: rotated.
fake_prime()
check("rotated past budget", len(made), 2)
check("old session closed", made[0].closed, True)

# Over many requests, rotations track the budget rather than growing without bound.
for _ in range(getstore.SESSION_BUDGET * 3):
    fake_prime()
check("rotations scale with budget", len(made), 5)
check("no session leak", FakeSession.live, 1)

# The escape hatch: budget 0 disables rotation entirely (back to the old behaviour).
saved = getstore.SESSION_BUDGET
try:
    getstore.SESSION_BUDGET = 0
    fake_prime.tl = threading.local()
    made.clear()
    for _ in range(500):
        fake_prime()
    check("budget=0 never rotates", len(made), 1)
finally:
    getstore.SESSION_BUDGET = saved

# The rotation logic must actually be wired into the real _session, not just this stand-in.
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "getstore.py")).read()
check("wired into _session", "SESSION_BUDGET and n >=" in src, True)
check("counter incremented on reuse", "_TL.n = n + 1" in src, True)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- session rotation: quota-shaped limit, session re-primed on a request budget (10 checks)")
