#!/usr/bin/env python3
"""adapt_test.py — prove the freeze actually holds, and that it lets go again.

A freeze that quietly fails is worse than no freeze: the experiment still runs, still prints arms, still
prints a verdict, and the verdict is fitted to a controller that moved underneath it. That is precisely
the class of failure the handoff calls the expensive one — a system reporting itself healthy while doing
nothing useful — so it is asserted at the layer where it acts, not at `adapt.frozen()`.

Each controller is driven with input that WOULD move it, and the assertion is that it did not move.

    python3 adapt_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapt
import blocks
import ladder
import pace
import sessions

fails = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r want %r" % (name, got, want))
        fails.append(name)


# ── the switch itself ────────────────────────────────────────────────────────────────────────────
adapt.clear()
os.environ.pop("ADAPT_FROZEN", None)
check("default is thawed — production must adapt", adapt.frozen(), False)
os.environ["ADAPT_FROZEN"] = "1"
check("env turns it on", adapt.frozen(), True)
os.environ["ADAPT_FROZEN"] = "0"
check("env 0 is off, not truthy-string on", adapt.frozen(), False)
os.environ.pop("ADAPT_FROZEN", None)
adapt.freeze()
check("in-process override beats the environment", adapt.frozen(), True)
adapt.clear()

# ── ladder: no costume rotation, no rung escalation, while frozen ────────────────────────────────
# Feeding it a solid wall of CAPTCHAs is exactly what makes it escalate; frozen, it must sit still.
def _blocked_run(source):
    l = ladder.SourceLadder(source, ladder.IMPERSONATE)
    for _ in range(ladder.WINDOW * 3):
        l.report(blocks.CAPTCHA)
    return l


adapt.freeze()
l = _blocked_run("probe-frozen")
check("frozen ladder holds its rung under 100% blocks", l.rung, ladder.IMPERSONATE)
check("frozen ladder records no moves", l.history, [])
adapt.unfreeze()
l = _blocked_run("probe-thawed")
check("thawed ladder DOES react (the freeze isn't just always-on)", len(l.history) > 0, True)

# ── sessions: the identity budget must not move while frozen ─────────────────────────────────────
adapt.freeze()
p = sessions.SessionPolicy("probe-frozen", budget=40)
for _ in range(50):
    p.report(blocks.CAPTCHA, 7)          # a burn at age 7 would normally crush the budget
check("frozen session budget is unchanged", p.budget, 40)
check("frozen report claims no burn", p.report(blocks.CAPTCHA, 7), False)
adapt.unfreeze()
p2 = sessions.SessionPolicy("probe-thawed", budget=40)
for _ in range(50):
    p2.report(blocks.CAPTCHA, 7)
check("thawed session budget DOES move", p2.budget != 40, True)

# ── pace: the rate must not move while frozen, but the counting continues ────────────────────────
adapt.freeze()
pc = pace.Pacer(10.0)
r0 = pc.rate
for _ in range(pc.window * 4):
    pc.report(False)                     # every response unusable — a thawed pacer backs off hard
check("frozen pacer holds its rate", pc.rate, r0)
check("frozen pacer records no backoffs", pc.backoffs, 0)
adapt.unfreeze()
pc2 = pace.Pacer(10.0)
for _ in range(pc2.window * 4):
    pc2.report(False)
check("thawed pacer DOES back off", pc2.rate < 10.0, True)
adapt.clear()

# ── and it lets go: a frozen run must not leave the controllers stuck ────────────────────────────
os.environ.pop("ADAPT_FROZEN", None)
check("after clear(), adaptation is live again", adapt.frozen(), False)
l = _blocked_run("probe-after")
check("the ladder reacts again after the freeze is lifted", len(l.history) > 0, True)

if fails:
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- adapt: the freeze holds all three controllers, and releases them")
