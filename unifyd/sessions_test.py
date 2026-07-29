"""A session is inventory that depreciates — and only real pushback counts as depreciation.

The binding constraint on our hardest source was not IPs, concurrency or rate. It was how many requests
one primed cookie may serve before the target stops answering. Across three runs, collapse tracked
request COUNT rather than elapsed time (~46 and ~58 per session), halving the rate bought only a longer
wait for the same wall, and re-priming every 40 requests took success from 5.7% to 88-93%.

Two things this pins.

FIRST: the budget must be LEARNED. `SESSION_BUDGET = 40` was the fifth constant this system leaned on,
and the previous four were each wrong within a day because every one was measured under conditions that
had already moved. A number right for this source today is not right next month or for a different
domain.

SECOND, and this is the one that would quietly destroy it: a closed store must not read as a burned
session. `not_found` is the target's catalogue, not our standing. Count it as depreciation and the budget
ratchets toward the floor on a source that is behaving perfectly — the same mistake that had the pacer
throttling itself to a standstill on dead stores.

Pure policy logic — no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocks  # noqa: E402
import sessions  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


sessions.reset()
p = sessions.SessionPolicy("t", budget=40)

# ── the budget gates re-priming ──────────────────────────────────────────────────────────────────
check("fresh session is not spent", p.spent(1), False)
check("under budget is not spent", p.spent(39), False)
check("at budget is spent", p.spent(40), True)
check("over budget is spent", p.spent(99), True)

# ── the catalogue is NOT our standing ────────────────────────────────────────────────────────────
for _ in range(50):
    p.report(blocks.NOT_FOUND, age=35)
check("closed stores are not burns", p.stats()["burns"], 0)
check("closed stores don't move the budget", p.budget, 40)
for _ in range(50):
    p.report(blocks.EMPTY, age=35)
check("real-but-empty is not a burn", p.budget, 40)
for _ in range(20):
    p.report(blocks.TIMEOUT, age=35)
check("timeouts are not identity burns", p.budget, 40)

# ── real pushback teaches where the wall is, and we stay under it ────────────────────────────────
for _ in range(8):
    p.report(blocks.SOFT_BLOCK, age=22)
check("burns are counted", p.stats()["burns"], 8)
check("observed burn learned", round(p.stats()["observed_burn"]), 22)
check("budget lands under the wall", p.budget < 22, True)
check("...but not at zero", p.budget >= 5, True)

# a captcha is a burn too, and a much later wall raises the budget back up
q = sessions.SessionPolicy("t2", budget=10)
for _ in range(12):
    q.report(blocks.CAPTCHA, age=200)
check("captcha counts as a burn", q.stats()["burns"], 12)
check("a later wall raises the budget", q.budget > 10, True)
check("bounded above", q.budget <= 500, True)

# ── the registry is the playbook ─────────────────────────────────────────────────────────────────
sessions.reset()
import source_registry  # noqa: E402
declared = [s.get("session_budget") for s in source_registry.SOURCES if s.get("id") == "ubereats"]
check("ubereats declares a budget", declared and declared[0] == 40, True)
check("policy reads the registry", sessions.policy_for("ubereats").budget, 40)
check("unknown source gets the default", sessions.policy_for("nope").budget, sessions.DEFAULT_BUDGET)
check("policy is per-source, and stable", sessions.policy_for("ubereats") is sessions.policy_for("ubereats"), True)

# ── wired into the fetcher, not just declared ────────────────────────────────────────────────────
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "getstore.py")).read()
check("fetcher consults the policy", "sessions.policy_for(site)" in src, True)
check("fetcher reports burns back", ".report(cls, getattr(_TL" in src, True)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- sessions: budget learned from real burns, never from a closed store (21 checks)")
