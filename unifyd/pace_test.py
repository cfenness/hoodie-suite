"""Pace by request RATE, and back off faster than you creep up.

Why this exists: worker-count tuning was wrong three times in one day, because workers are a proxy that
breaks whenever the work per worker changes. The decisive measurement — 120 workers across 20 ISP IPs
(6 per IP, modest) collapsed after ~5,578 requests in 85 seconds, 5,025 of them empty. A per-IP limit
would have absorbed that; an aggregate rate limit is what we actually hit.

And why it self-tunes: every constant we picked was wrong within a day. 3/IP was calibrated during a
throttle so it read the floor of the failure; 6/IP was calibrated while enrichment padded each worker's
think-time, so it was far too fast once the sweep got lean. A fixed number cannot track a limit that
moves with load, time of day, and how hot the IPs already are.

The control law's asymmetry is the point: slow is recoverable, throttled-and-lying is not.

Pure control logic — no network, no sleeping on real time.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pace  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


# ── the fleet budget splits across shards without coordination ───────────────────────────────────
check("8 shards split 40/s", round(pace.shard_rate(8, 40.0), 2), 5.0)
check("1 shard takes it all", round(pace.shard_rate(1, 40.0), 2), 40.0)
check("never zero", pace.shard_rate(10_000, 40.0) > 0, True)

# ── MULTIPLICATIVE DECREASE: one bad window halves the rate ──────────────────────────────────────
p = pace.Pacer(rate=8.0, window=10, empty_trip=0.25)
for _ in range(10):
    p.report(ok=False)
check("bad window halves", p.rate, 4.0)
check("backoff counted", p.stats()["backoffs"], 1)

# repeated failure keeps halving, down to a floor that still makes progress
for _ in range(200):
    p.report(ok=False)
check("floors, never zero", p.rate >= 0.5, True)
check("floor is reported", p.stats()["at_floor"] > 0, True)

# ── ADDITIVE INCREASE: healthy windows creep, they don't lunge ───────────────────────────────────
q = pace.Pacer(rate=5.0, window=10, empty_trip=0.25)
for _ in range(10):
    q.report(ok=True)
check("healthy window creeps by 0.5", q.rate, 5.5)
# ten good windows must not multiply the rate
for _ in range(90):
    q.report(ok=True)
check("creep stays bounded", q.rate <= 5.0 * 4, True)

# ── a mixed window under the trip ratio is still healthy ─────────────────────────────────────────
r = pace.Pacer(rate=10.0, window=10, empty_trip=0.25)
for i in range(10):
    r.report(ok=(i > 0))            # 1 of 10 empty = 10%, under the 25% trip
check("10% empties is not a backoff", r.rate > 10.0, True)

# at the trip ratio exactly, back off — the boundary belongs to the safe side
t = pace.Pacer(rate=10.0, window=4, empty_trip=0.25)
for i in range(4):
    t.report(ok=(i > 0))            # 1 of 4 = 25%
check("at the trip ratio it backs off", t.rate, 5.0)

# ── the token bucket actually limits throughput ──────────────────────────────────────────────────
g = pace.Pacer(rate=20.0)
t0 = time.time()
for _ in range(10):
    g.acquire()
el = time.time() - t0
check("10 tokens at 20/s takes ~0.5s, not 0", el > 0.2, True)
check("and is not absurdly slow", el < 3.0, True)

# ── install/get give one shared controller ───────────────────────────────────────────────────────
inst = pace.install(3.0)
check("install returns the pacer", pace.get() is inst, True)
check("installed rate", inst.rate, 3.0)

# ── the BASELINE case that broke the first paced run ─────────────────────────────────────────────
# ~25% of stores return no catalog because they are closed/delisted. That is a property of the
# universe, not a signal about our request rate — and a fixed 0.25 trip read it as constant overload,
# halving every window until the controller floored itself at ~2 stores/s (a 35-hour projection) while
# nothing was throttling it. A steady background must let the rate CLIMB.
b = pace.Pacer(rate=5.0, window=10)
for _ in range(12):
    for i in range(10):
        b.report(ok=(i >= 3))            # a steady 30% empty background
check("steady background does not back off", b.stats()["backoffs"], 0)
check("steady background lets rate climb", b.rate > 5.0, True)
check("trip derived above the baseline", b.stats()["trip"] > 0.3, True)

# ...and a REAL throttle (90% empty, as measured: 5,025 of 5,578) must still trip hard.
c = pace.Pacer(rate=5.0, window=10)
for _ in range(4):
    for i in range(10):
        c.report(ok=(i >= 3))            # calibrate on the same background
warm = c.rate
for _ in range(2):
    for i in range(10):
        c.report(ok=(i >= 9))            # then 90% empty
check("a real throttle still backs off", c.rate < warm, True)
check("and is counted", c.stats()["backoffs"] >= 1, True)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("── %d failed" % len(fails))
    sys.exit(1)
print("── pace: rate-limited, backs off hard, creeps up slow (16 checks)")
