#!/usr/bin/env python3
"""costume_probe_test.py — feed the analysis data with a KNOWN cause and check it names that cause.

The live half of the probe cannot be tested without the target, but the live half is not where a silent
bug lives. The dangerous layer is the one that turns outcomes into a verdict: it always produces an
answer, the answer always looks reasonable, and a wrong one sends the next day's work at pacing when the
problem is rotation. Three tests this week asserted one layer away from the bug and passed while the
feature did nothing — so these assert on the verdict itself.

Each case synthesises arms from a ground truth we choose, then requires the analysis to recover it,
INCLUDING the null case: a flat experiment must come back "no effect detected", never a hedge that reads
like a finding.

    python3 costume_probe_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costume_probe as P

fails = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        fails.append(name)


def arm(workers, n, pct, exits=("10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"), decay=None):
    """One arm at a given usable percentage, spread EVENLY over the arm unless `decay` is given.

    Even spreading matters: a bunched arm would trip the within-arm decay test and contaminate the
    arm-level cases with a signal they are not testing."""
    recs = []
    k = int(round(n * pct / 100.0))
    for i in range(n):
        if decay is None:
            good = int((i + 1) * k / float(n)) > int(i * k / float(n))
        else:
            # Linear fall from `pct` to `decay` across the arm.
            frac = pct + (decay - pct) * (i / float(max(1, n - 1)))
            good = ((i * 997) % 100) < frac
        recs.append((i, exits[i % len(exits)], "ok" if good else "captcha"))
    return {"workers": workers, "costume": "safari17_0", "elapsed_s": 10.0, "records": recs}


print("statistics")
z = P.two_prop_z(95, 100, 55, 100)
check("two_prop_z separates 95% from 55%", z is not None and z > 5, "z=%s" % z)
check("two_prop_z is ~0 for identical arms", abs(P.two_prop_z(80, 100, 80, 100)) < 1e-9)
check("two_prop_z is None on an empty arm", P.two_prop_z(0, 0, 5, 10) is None)
check("one_prop_z signs a shortfall negative", P.one_prop_z(50, 100, 0.9) < 0)
mdd_small, mdd_big = P.min_detectable_pp(20, 20), P.min_detectable_pp(2000, 2000)
check("more requests detect smaller differences", mdd_small > mdd_big,
      "n=20 -> %s pp, n=2000 -> %s pp" % (mdd_small, mdd_big))
check("p_value of a large z is ~0", P.p_value(6.0) < 0.001)

print("usable classification")
check("`empty` counts as usable (a store with nothing in it is covered)", P._usable("empty"))
check("`unknown` does NOT count as usable", not P._usable("unknown"))
check("`soft_block` does NOT count as usable", not P._usable("soft_block"))

print("ground truth: pure CONCURRENCY (arms differ, control flat)")
v = P.analyse([arm(2, 200, 95), arm(8, 200, 92), arm(16, 200, 55), arm(2, 200, 95)])["verdict"]
check("verdict is concurrency", v["hypothesis"] == "concurrency", v["hypothesis"])
check("control replicate reads as flat", not v["control"]["significant"],
      "drop=%s pp" % v["control"]["drop_pp"])
check("the 16-worker arm is the one flagged",
      [r["workers"] for r in v["concurrency"]["arms"] if r["significant"]] == [16])
check("prescription is pacing, not rotation", "pace" in (v["prescription"] or ""))

print("ground truth: STEP CHANGE at CONSTANT concurrency (the mislabeling bug found 2026-07-29)")
# Real numbers off a `2,2,2,2` run: every arm at 2 workers, run specifically to hold concurrency at
# zero variation. usable% still stepped from ~97% to ~26% mid-run. The interior arms carry no
# concurrency information — nothing on this plan ever touched worker count — so this must never
# surface as "concurrency", and it did before this fix.
v = P.analyse([arm(2, 400, 97.5), arm(2, 400, 96.8), arm(2, 400, 31.0), arm(2, 400, 26.2)])["verdict"]
check("verdict is cumulative", v["hypothesis"] == "cumulative", v["hypothesis"])
check("NO interior arm is filed as a concurrency effect (concurrency never varied on this plan)",
      not any(r["significant"] for r in v["concurrency"]["arms"]), str(v["concurrency"]["arms"]))
check("the step shows up as a trajectory finding instead",
      bool(v.get("trajectory") and any(r["significant"] for r in v["trajectory"]["arms"])),
      str(v.get("trajectory")))
check("evidence describes a step", any("step" in e.lower() for e in v["evidence"]), str(v["evidence"]))
check("evidence never uses the concurrency-arm phrasing (that arm type is empty here)",
      not any("time-adjusted baseline" in e for e in v["evidence"]), str(v["evidence"]))

print("ground truth: pure CUMULATIVE (monotone decline, worker count irrelevant)")
v = P.analyse([arm(2, 200, 95), arm(8, 200, 75), arm(16, 200, 55), arm(2, 200, 35)])["verdict"]
check("verdict is cumulative", v["hypothesis"] == "cumulative", v["hypothesis"])
check("control replicate caught the drift", v["control"]["significant"],
      "drop=%s pp" % v["control"]["drop_pp"])
check("no arm is blamed on concurrency once time is factored out",
      not any(r["significant"] for r in v["concurrency"]["arms"]),
      str(v["concurrency"]["arms"]))
check("prescription is rotation, not pacing", "rotate" in (v["prescription"] or ""))

print("ground truth: BOTH (drift over time AND a concurrency penalty on top)")
v = P.analyse([arm(2, 300, 95), arm(8, 300, 80), arm(16, 300, 30), arm(2, 300, 70)])["verdict"]
check("verdict is both", v["hypothesis"] == "both", v["hypothesis"])

print("ground truth: NOTHING (a flat experiment must not manufacture a finding)")
res = P.analyse([arm(2, 200, 93), arm(8, 200, 93), arm(16, 200, 93), arm(2, 200, 93)])
v = res["verdict"]
check("verdict is no_effect_detected", v["hypothesis"] == "no_effect_detected", v["hypothesis"])
check("the null result states what it could have detected",
      any("could detect" in c for c in v["caveats"]), str(v["caveats"]))
check("a null result points at fleet-aggregate load, not per-shard workers",
      any("AGGREGATE" in c for c in v["caveats"]))

print("small effects below the threshold are not findings")
v = P.analyse([arm(2, 4000, 90), arm(8, 4000, 85), arm(16, 4000, 85), arm(2, 4000, 90)])["verdict"]
check("a 5 pp effect stays under the %g pp bar even when statistically significant" % P.MIN_EFFECT_PP,
      v["hypothesis"] == "no_effect_detected", v["hypothesis"])

print("within-arm decay is detected even when arm averages are flat")
a1, a2 = arm(2, 300, 95), arm(2, 300, 95, decay=40)
check("an evenly-spread arm shows no internal decay", not P.arm_summary(a1)["internal_decay"]["significant"])
d = P.arm_summary(a2)["internal_decay"]
check("a decaying arm is flagged internally", d["significant"], str(d))
v = P.analyse([arm(2, 300, 95, decay=45), arm(8, 300, 95, decay=45),
               arm(2, 300, 95, decay=45)])["verdict"]
check("flat arms + internal decay still reads as cumulative", v["hypothesis"] == "cumulative",
      v["hypothesis"])
check("...and says why", any("WITHIN arms" in e for e in v["evidence"]), str(v["evidence"]))

print("a missing control replicate is declared, not papered over")
v = P.analyse([arm(2, 200, 95), arm(8, 200, 90), arm(16, 200, 60)])["verdict"]
check("caveat names the missing replicate", any("no control replicate" in c for c in v["caveats"]),
      str(v["caveats"]))

print("exit attribution: a burned subset vs a uniform decline")
burned = {"records": [], "workers": 8, "costume": "x", "elapsed_s": 1.0}
for i in range(400):
    ex = "10.0.0.%d" % (i % 10)
    bad = (i % 10) in (0, 1) or (i % 37 == 0)      # two exits dead, everyone else mostly fine
    burned["records"].append((i, ex, "captcha" if bad else "ok"))
ep = P.exit_pattern(P.by_exit([burned]))
check("a burned subset is named as such", ep["pattern"] == "burned_subset", str(ep))
check("the burned exits are listed", set(ep["below_median_25pp"]) == {"10.0.0.0", "10.0.0.1"}, str(ep))

uni = {"records": [], "workers": 8, "costume": "x", "elapsed_s": 1.0}
for i in range(400):
    # The outcome must be INDEPENDENT of which exit served it — `i % 2` alongside `i % 10` would put
    # every failure on the even exits and correctly read as a split pool, testing the opposite thing.
    good = ((i * 2654435761) % 97) < 48
    uni["records"].append((i, "10.0.0.%d" % (i % 10), "ok" if good else "captcha"))
ep = P.exit_pattern(P.by_exit([uni]))
check("an even decline is named uniform", ep["pattern"] == "uniform", str(ep))
check("too few exits is not a pattern", P.exit_pattern({"a": {"good": 1, "total": 9, "pct": 11.1}})
      ["pattern"] == "insufficient")

print("")
print("FAILED: %s" % ", ".join(fails) if fails else "all checks passed")
sys.exit(1 if fails else 0)
