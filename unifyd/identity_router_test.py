#!/usr/bin/env python3
"""identity_router_test.py — the selection logic is the whole product; test it directly, no network.

Every case passes a synthetic `now` so quarantine expiry and hot-window decay are exercised without
sleeping — the same discipline `pace_test`/`sessions_test` use.

    python3 identity_router_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocks as B
import identity_router as R

fails = []


def check(name, got, want=True):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r want %r" % (name, got, want))
        fails.append(name)


def pool(n):
    return ["user:pass@10.0.0.%d:8080" % i for i in range(n)]


print("record() classifies correctly")
r = R.Router()
r.record("10.0.0.1", "safari17_0", B.OK, now=0)
r.record("10.0.0.1", "safari17_0", B.CAPTCHA, now=1)
p = r._pairs[("10.0.0.1", "safari17_0")]
check("one good, one throttled recorded", len(p.recent), 2)
check("a single throttle doesn't quarantine (needs a STREAK)", p.quarantined_until, 0)

print("quarantine after a streak, never on background noise")
r = R.Router()
for i in range(R.QUARANTINE_STREAK):
    r.record("10.0.0.2", "safari17_0", B.CAPTCHA, now=float(i))
p = r._pairs[("10.0.0.2", "safari17_0")]
check("a full streak quarantines the pair", p.quarantined_until > 0)
r2 = R.Router()
for i in range(20):
    r2.record("10.0.0.3", "safari17_0", B.NOT_FOUND, now=float(i))
p2 = r2._pairs[("10.0.0.3", "safari17_0")]
check("not_found never quarantines, however many in a row", p2.quarantined_until, 0)
r3 = R.Router()
for i in range(20):
    r3.record("10.0.0.4", "safari17_0", B.TIMEOUT, now=float(i))
check("timeout never quarantines either", r3._pairs[("10.0.0.4", "safari17_0")].quarantined_until, 0)

print("a real success earns back the fast cooldown")
r = R.Router()
for i in range(R.QUARANTINE_STREAK):
    r.record("10.0.0.5", "c", B.CAPTCHA, now=float(i))
p = r._pairs[("10.0.0.5", "c")]
escalated = p.quarantine_s
r.record("10.0.0.5", "c", B.OK, now=1000)     # after the quarantine window, a real success lands
check("quarantine_s reset to the base after a success", p.quarantine_s, R.QUARANTINE_BASE_S)
check("(it had actually escalated above base first)", escalated > R.QUARANTINE_BASE_S)

print("repeated streaks escalate the cooldown, capped")
r = R.Router()
seen = []
t = 0.0
for _round in range(6):
    for _ in range(R.QUARANTINE_STREAK):
        r.record("10.0.0.6", "c", B.CAPTCHA, now=t)
        t += 1
    p = r._pairs[("10.0.0.6", "c")]
    seen.append(p.quarantine_s)
    t = p.quarantined_until + 0.01           # let it expire before the next streak
check("cooldown grows across repeated streaks (%s)" % seen, seen == sorted(seen))
check("cooldown is capped", max(seen) <= R.QUARANTINE_MAX_S)

print("pick() avoids a quarantined pair in favour of a healthy one")
r = R.Router()
for i in range(R.QUARANTINE_STREAK):
    r.record("10.0.0.1", "c", B.CAPTCHA, now=float(i))     # quarantined_until ~= 2 + QUARANTINE_BASE_S
# 10.0.0.2's good history sits well BEFORE the hot window relative to the pick below, so it reads as
# healthy (scoring) without ALSO reading as busy-right-now (hotness) — those are deliberately two
# different timestamp lists (see the module docstring), and conflating them here would test the wrong
# thing: a real exit's history ages out of "hot" long before it stops being evidence for "healthy".
for i in range(10):
    r.record("10.0.0.2", "c", B.OK, now=-100.0 + i)
px, costume = r.pick(["u@10.0.0.1:1", "u@10.0.0.2:1"], ["c"], now=10.0)
check("healthy exit chosen over the still-quarantined one", px, "u@10.0.0.2:1")

print("pick() avoids a HOT exit even with no bad history — concentration avoidance")
r = R.Router()
for _ in range(R.HOT_MAX + 1):
    r.record("10.0.0.1", "c", B.OK, now=10.0)   # all recent, all good — busy, not unhealthy
px, costume = r.pick(["u@10.0.0.1:1", "u@10.0.0.2:1"], ["c"], now=10.5)
check("the cold exit is chosen over the hot-but-healthy one", px, "u@10.0.0.2:1")

print("hotness decays — the same exit becomes pickable again once the window passes")
px, costume = r.pick(["u@10.0.0.1:1", "u@10.0.0.2:1"], ["c"], now=10.0 + R.HOT_WINDOW_S + 1)
check("after the hot window elapses, either exit is fair game",
      px in ("u@10.0.0.1:1", "u@10.0.0.2:1"))

print("never deadlocks: if EVERYTHING is quarantined, pick() still returns something")
r = R.Router()
for ip in ("10.0.0.1", "10.0.0.2"):
    for i in range(R.QUARANTINE_STREAK):
        r.record(ip, "c", B.CAPTCHA, now=float(i))
px, costume = r.pick(["u@10.0.0.1:1", "u@10.0.0.2:1"], ["c"], now=5.0)
check("a pick is still returned rather than None", px is not None)

print("an untried pair scores neutral, not zero — exploration can find it")
r = R.Router()
for _ in range(15):
    r.record("10.0.0.1", "c", B.CAPTCHA, now=1.0)   # a thoroughly bad, well-sampled pair
score_bad, n_bad = r._score(("10.0.0.1", "c"), now=2.0)
score_new, n_new = r._score(("10.0.0.9", "c"), now=2.0)
check("an untried pair (%.3f) scores ABOVE a proven-bad, well-sampled one (%.3f)"
      % (score_new, score_bad), score_new > score_bad)

print("cold start: N threads racing to prime BEFORE any outcome returns must NOT all collide on one "
      "exit (real bug found live 2026-07-29 — 10 of 13 total picks landed on one pair in a 400-request "
      "run and it died: 0% across 20 outcomes, because hotness only counted record()'d outcomes, which "
      "arrive a full round-trip after the picks that caused the collision)")
r = R.Router()
picks = [r.pick(pool(10), ["c"], now=0.0) for _ in range(10)]   # simulates 10 threads' first prime,
                                                                  # same instant, zero outcomes yet
exits_used = [px for px, _c in picks]
check("at least HOT_MAX+1 distinct exits were used, not one (%d distinct of %d picks)"
      % (len(set(exits_used)), len(exits_used)), len(set(exits_used)) >= R.HOT_MAX + 1)
check("no single exit took more than HOT_MAX of the 10 picks (%s)"
      % {e: exits_used.count(e) for e in set(exits_used)},
      max(exits_used.count(e) for e in set(exits_used)) <= R.HOT_MAX)

print("empty pool or costume list returns (None, None) rather than raising")
check("empty pool", R.get().pick([], ["c"]), (None, None))
check("empty costumes", R.get().pick(["u@10.0.0.1:1"], []), (None, None))

print("reset() clears all state")
r = R.Router()
r.record("10.0.0.1", "c", B.OK, now=1.0)
r.reset()
check("no pairs after reset", len(r._pairs), 0)
check("no exit activity after reset", len(r._exit_ts), 0)

print("hot_exits() names exits touched within the window, busiest first — the fleet_watchdog signal")
r = R.Router()
r.record("10.0.0.1", "c", B.OK, now=10.0)
r.record("10.0.0.1", "c", B.OK, now=10.1)
r.record("10.0.0.2", "c", B.OK, now=10.2)
r.record("10.0.0.3", "c", B.OK, now=10.0 - R.HOT_WINDOW_S - 5)   # long stale — outside the window
hot = r.hot_exits(top=8, now=10.5)
check("stale exit not reported", "10.0.0.3" not in [ip for ip, _n in hot])
check("both recent exits reported", sorted(ip for ip, _n in hot), ["10.0.0.1", "10.0.0.2"])
check("busiest (2 touches) sorts first", hot[0][0], "10.0.0.1")
check("top= caps the result", len(R.Router().hot_exits(top=1, now=0)) <= 1)
check("an untouched router reports no hot exits", R.Router().hot_exits(now=0), [])

print("module-level record()/pick() operate on the shared global router")
R.reset()
R.record("10.0.0.1", "c", B.OK, now=1.0)
check("global router recorded it", ("10.0.0.1", "c") in R.get()._pairs)

if fails:
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- identity_router: pick the (exit, costume) that's healthy right now, never a cached list")
