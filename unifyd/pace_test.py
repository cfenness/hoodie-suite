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
check("install's default max_rate is still rate*4 (unchanged for existing callers)",
      inst.max_rate, 12.0)

# an explicit max_rate must reach the Pacer, not just the default rate*4 — observed live 2026-08-02:
# two DoorDash runs (starting at 20 and 40 req/s) each converged EXACTLY at 4x their own starting
# rate with zero backoffs, because nothing overrode this default; the target's real ceiling was
# never actually found.
inst2 = pace.install(20.0, max_rate=500.0)
check("explicit max_rate reaches the pacer instead of defaulting to rate*4", inst2.max_rate, 500.0)

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

# ── THE DEADLOCK: a rate below 1.0/s must still issue tokens ─────────────────────────────────────
# The bucket capacity was `self.rate`, so below 1.0/s tokens could never reach the 1.0 needed to issue
# a request. acquire() spun forever, and since the heartbeat only fires when a store completes, the run
# FROZE while still reporting itself alive: 8 shards, zero progress for 4 minutes, every counter static.
# min_rate is 0.5, so one backoff to the floor hung a shard permanently.
#
# The original acquire() test used rate=20 — comfortably above 1.0 — so it never touched the region
# where the bug lived. A test aimed away from the failure is how this shipped.
slow = pace.Pacer(rate=0.5)
t0 = time.time()
slow.acquire(); slow.acquire()
el = time.time() - t0
check("sub-1.0 rate still issues tokens", el < 6.0, True)
check("...and is actually rate-limited", el > 1.0, True)

floored = pace.Pacer(rate=0.5, min_rate=0.5, window=10)
for _ in range(400):
    floored.report(ok=False)          # drive it to the floor
t0 = time.time()
floored.acquire()
check("a floored pacer never deadlocks", time.time() - t0 < 6.0, True)

# ── CALIBRATING UNDER DURESS: a learned baseline needs a ceiling ─────────────────────────────────
# Observed live: a run began 82% CAPTCHA-challenged, learned 0.82 as its "normal" background, set the
# trip at 0.82+0.25 = 1.07, and became mathematically incapable of firing — the rate CLIMBED to 6.65/s
# while 82% of responses were challenges. This is the same error as calibrating 3/IP during a throttle,
# which pace.py's own comment warns about; the warning existed and was not applied to the calibration.
# Above BASELINE_MAX we are not measuring a background, we are measuring a wall.
blocked = pace.Pacer(rate=5.0, window=10)
for _ in range(6):
    for i in range(10):
        blocked.report(ok=(i >= 8))          # 80% blocked from the very first window
check("a run starting blocked still trips", blocked.stats()["backoffs"] > 0, True)
check("...and backs the rate down", blocked.rate < 5.0, True)
check("...using the fixed floor, not the learned wall", blocked.empty_trip, pace.TRIP_FLOOR)

# and the healthy case must be unaffected — it still learns its own background and climbs
healthy2 = pace.Pacer(rate=5.0, window=10)
for _ in range(6):
    for i in range(10):
        healthy2.report(ok=(i >= 3))         # 30% dead-store background
check("healthy background still climbs", healthy2.rate > 5.0, True)
check("healthy background never trips", healthy2.stats()["backoffs"], 0)
check("healthy trip sits above its baseline", healthy2.empty_trip > 0.5, True)

# ── UE_PACE_MAX_MULT decouples the climb ceiling from the starting seed ──────────────────────────
# Observed live on the 2026-07-30 UberEats sweep: 2 of 8 shards sat pinned at exactly rate*4 for the
# run's full multi-day duration, with the BEST success rates in the fleet — evidence the ceiling was
# our own hardcoded multiplier, not a real target wall. Default (no env var) must stay 4, so an
# existing deployment's behavior is unchanged unless it opts in.
check("default multiplier is unchanged", pace.Pacer(rate=5.0).max_rate, 20.0)

os.environ["UE_PACE_MAX_MULT"] = "8"
import importlib
importlib.reload(pace)
check("env override widens the ceiling", pace.Pacer(rate=5.0).max_rate, 40.0)
del os.environ["UE_PACE_MAX_MULT"]
importlib.reload(pace)          # restore default for anything imported after this module
check("restored default after reload", pace.Pacer(rate=5.0).max_rate, 20.0)

# an explicit max_rate= still wins over the multiplier, same as before this change
check("explicit max_rate overrides the multiplier", pace.Pacer(rate=5.0, max_rate=9.0).max_rate, 9.0)

# ── utilization meter: does it tell WORKER-bound apart from PACER-bound? ─────────────────────────
# This is the whole reason the meter exists. If it can't distinguish "the budget is going unspent"
# from "the ceiling is the constraint", it cannot answer whether adding concurrency would help — and
# that question has been answered by guesswork twice already (see pace.py's own header).
# Simulating SLOW WORKERS, not a slow pacer: the ceiling is generous and demand arrives with gaps, so
# tokens are always already waiting. (Acquiring back-to-back would NOT model this — an empty bucket
# blocks at the refill rate no matter how high the ceiling, which reads as pacer-bound and is exactly
# the confusion this meter exists to resolve.)
u = pace.Pacer(rate=50.0)
for _ in range(5):
    u.acquire()
    time.sleep(0.05)                 # think-time between requests — the worker is the slow part
s_u = u.stats()
check("an unused budget still counts its tokens", s_u["achieved"] > 0, True)
check("...and reads far below the ceiling", s_u["utilization"] < 0.5, True)
check("...with nobody waiting: the pacer was not the constraint", s_u["wait_ms"] <= 1, True)

# The opposite case: a tight ceiling with back-to-back demand must read as pacer-bound.
b_u = pace.Pacer(rate=2.0)
t0 = time.time()
for _ in range(4):
    b_u.acquire()
el_u = time.time() - t0
s_b = b_u.stats()
check("a saturated budget actually blocks", el_u > 0.4, True)
check("...and the wait is visible in the meter", s_b["wait_ms"] > 0, True)
check("...with achieved tracking the ceiling, not demand", s_b["achieved"] <= b_u.rate * 1.5, True)

# The window resets on read so a heartbeat sees NOW; cumulative counters must not.
c_u = pace.Pacer(rate=50.0)
c_u.acquire()
first_u = c_u.stats()
second_u = c_u.stats()
check("a fresh window reports less traffic than the one before it",
      second_u["achieved"] < first_u["achieved"], True)
check("backoffs stay cumulative across reads", second_u["backoffs"], first_u["backoffs"])

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("── %d failed" % len(fails))
    sys.exit(1)
print("── pace: rate-limited, backs off hard, creeps up slow (20 checks)")
