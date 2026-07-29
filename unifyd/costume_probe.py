#!/usr/bin/env python3
"""costume_probe.py — is the degradation CONCURRENCY or CUMULATIVE? A controlled experiment, not a run.

THE QUESTION. `impersonate="safari17_0"` returns 4/4 usable on a single sequential probe and ~51% usable
under 8-shard fleet load. The costume works and degrades with scale, and there are two very different
reasons it could:

  CONCURRENCY — usable% is a function of requests in flight. The fix is pacing: fewer workers, same total.
  CUMULATIVE  — usable% is a function of requests already spent on an identity (a quota). The fix is
                rotation: same workers, more identities.

Those prescriptions are opposites. Guessing wrong costs a day — it already has, four times, because every
previous model was fitted to a blended signal and disproved only by the next run.

THE DESIGN. Hold the total request count per arm constant and vary only the worker count, then read three
marginals off the SAME requests:

  by arm      -> the concurrency marginal
  by ordinal  -> the cumulative marginal (does usable% fall from the first third of an arm to the last?)
  by exit IP  -> is it a burned SUBSET of identities, or a uniform decline? They look identical in an
                 average, and they have different fixes.

THE CONTROL IS THE POINT. Arms run low -> high -> low, and the FINAL arm repeats the FIRST arm's worker
count. If arm-last matches arm-first, elapsed time and accumulated volume are not driving the result and
a difference between arms is really about concurrency. If arm-last is much worse than arm-first at the
same worker count, the decline is cumulative and the concurrency comparison is confounded — so when that
happens this does not report a concurrency verdict from a raw arm comparison; it tests each arm against
the baseline INTERPOLATED between the two control arms instead.

NOTHING ADAPTS DURING A MEASUREMENT. `adapt.freeze()` holds the pacer, the session budget and the ladder
still for the duration. A ladder that rotates costume at 50% blocked would hand back a clean arm for a
reason the experiment never recorded — a good number that nothing earned, which is the failure mode that
has cost more here than the scrapers breaking.

A NULL RESULT IS REPORTED AS A NULL RESULT. If no effect clears significance, the verdict says so and
prints the minimum difference this sample size could have detected, so "no effect" is never confused with
"not enough requests to see one".

    # the 20-minute experiment
    flyctl ssh console -a hoodie-suite -C \\
      "python3 /app/unifyd/costume_probe.py --arms 2,8,16,2 --per-arm 120"

    # do different costumes degrade differently? (same plan, once per costume)
    ... --costumes safari17_0,firefox133,edge101 --arms 2,16,2 --per-arm 80
"""
import argparse
import concurrent.futures as cf
import json
import math
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import blocks as _blocks                 # stdlib-only; safe to import at module scope

# Effect sizes worth acting on. A 5-point wobble changes no decision; a 20-point drop changes the whole
# remediation. Stated up front so the verdict is measured against a threshold chosen before the data.
MIN_EFFECT_PP = float(os.environ.get("PROBE_MIN_EFFECT_PP", "10"))
ALPHA_Z = 1.96                       # two-sided 95%


# ── analysis (pure — no network, no clock; this is the part that can be silently wrong) ──────────────

def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def two_prop_z(k1, n1, k2, n2):
    """Two-proportion z for k1/n1 vs k2/n2. None when either arm is empty. Positive z = arm 1 higher."""
    if not n1 or not n2:
        return None
    p1, p2 = k1 / float(n1), k2 / float(n2)
    p = (k1 + k2) / float(n1 + n2)
    se = math.sqrt(p * (1.0 - p) * (1.0 / n1 + 1.0 / n2))
    if se <= 0:
        return 0.0
    return (p1 - p2) / se


def one_prop_z(k, n, p0):
    """z for an observed k/n against an expected proportion p0."""
    if not n or p0 <= 0 or p0 >= 1:
        return None
    se = math.sqrt(p0 * (1.0 - p0) / n)
    return ((k / float(n)) - p0) / se if se > 0 else 0.0


def p_value(z):
    return None if z is None else round(2.0 * (1.0 - _phi(abs(z))), 4)


def min_detectable_pp(n1, n2, p=0.5):
    """The smallest difference in percentage points this sample could have called significant at 95%/80%.

    Reported alongside every null result. "No significant effect" from 20 requests and from 2,000 are
    completely different statements, and only one of them is evidence."""
    if not n1 or not n2:
        return None
    se = math.sqrt(p * (1.0 - p) * (1.0 / n1 + 1.0 / n2))
    return round(100.0 * (ALPHA_Z + 0.84) * se, 1)      # 0.84 = z for 80% power


def _usable(cls):
    """Usable = we got the payload we asked for. `empty` counts: the store answered and had nothing,
    which is coverage, not pushback. Everything else — including `unknown` — does not."""
    return cls in ("ok", "empty")


def arm_summary(arm):
    """One arm's outcome. `arm` is {'workers': W, 'costume': c, 'records': [(ordinal, exit, cls), ...]}."""
    recs = arm.get("records") or []
    n = len(recs)
    good = sum(1 for _o, _e, c in recs if _usable(c))
    classes = {}
    for _o, _e, c in recs:
        classes[c] = classes.get(c, 0) + 1
    # Within-arm decay: first third vs last third, in completion order. A quota shows up here even when
    # the arm average still looks acceptable.
    third = n // 3
    early = recs[:third]
    late = recs[-third:] if third else []
    ek = sum(1 for _o, _e, c in early if _usable(c))
    lk = sum(1 for _o, _e, c in late if _usable(c))
    z_in = two_prop_z(ek, len(early), lk, len(late)) if third else None
    return {
        "workers": arm.get("workers"),
        "costume": arm.get("costume"),
        "n": n,
        "usable": good,
        "usable_pct": round(100.0 * good / n, 1) if n else None,
        "elapsed_s": arm.get("elapsed_s"),
        "req_per_s": (round(n / arm["elapsed_s"], 2) if arm.get("elapsed_s") else None),
        "classes": classes,
        "internal_decay": {
            "early_pct": round(100.0 * ek / len(early), 1) if early else None,
            "late_pct": round(100.0 * lk / len(late), 1) if late else None,
            "z": (round(z_in, 2) if z_in is not None else None),
            "p": p_value(z_in),
            # Decay means EARLY was significantly better than LATE.
            "significant": bool(z_in is not None and z_in > ALPHA_Z),
            "min_detectable_pp": min_detectable_pp(len(early), len(late)),
        },
    }


def by_exit(arms):
    """Per-exit usable% pooled across arms, plus whether the damage is concentrated or spread.

    A burned subset and a uniform decline produce the same average and want opposite fixes: retire six
    addresses, or stop blaming addresses at all."""
    agg = {}
    for a in arms:
        for _o, ex, c in (a.get("records") or []):
            if not ex:
                continue
            d = agg.setdefault(ex, {"good": 0, "total": 0})
            d["total"] += 1
            if _usable(c):
                d["good"] += 1
    for d in agg.values():
        d["pct"] = round(100.0 * d["good"] / max(1, d["total"]), 1)
    return agg


# The shape-of-the-distribution question is the same one a production run asks of its own tally, so it
# lives with the tally. Same code reading the probe and the run record means a finding here and a finding
# in a nightly sweep are directly comparable.
exit_pattern = _blocks.exit_pattern


def verdict(summaries):
    """Read the arms and say which hypothesis the evidence supports — or that it supports neither.

    `summaries` is the ordered list from arm_summary(). The first and last arms must share a worker
    count; that pair is the control."""
    out = {"hypothesis": "inconclusive", "control": None, "concurrency": None, "evidence": [],
           "caveats": []}
    if len(summaries) < 2:
        out["caveats"].append("fewer than 2 arms — nothing to compare")
        return out

    first, last = summaries[0], summaries[-1]
    control_valid = first["workers"] == last["workers"] and first["n"] and last["n"]
    drift_sig = False
    if control_valid:
        z = two_prop_z(first["usable"], first["n"], last["usable"], last["n"])
        drop_pp = round((first["usable_pct"] or 0) - (last["usable_pct"] or 0), 1)
        drift_sig = bool(z is not None and z > ALPHA_Z and drop_pp >= MIN_EFFECT_PP)
        out["control"] = {
            "workers": first["workers"], "first_pct": first["usable_pct"], "last_pct": last["usable_pct"],
            "drop_pp": drop_pp, "z": (round(z, 2) if z is not None else None), "p": p_value(z),
            "significant": drift_sig,
            "min_detectable_pp": min_detectable_pp(first["n"], last["n"]),
        }
    else:
        out["caveats"].append("no control replicate — first and last arm differ in worker count, so "
                              "elapsed-time effects cannot be separated from concurrency effects")

    # Concurrency. When the control drifted, a raw high-vs-low arm comparison is confounded by time, so
    # each arm is tested against the baseline interpolated between the two control arms by its position
    # in the request sequence. That removes the drift and leaves the worker-count effect.
    mids, cum, total = [], 0, sum(s["n"] for s in summaries)
    for s in summaries:
        mids.append((cum + s["n"] / 2.0) / max(1, total))
        cum += s["n"]
    p_start = (first["usable_pct"] or 0) / 100.0
    p_end = (last["usable_pct"] or 0) / 100.0

    interior = [(i, s) for i, s in enumerate(summaries) if 0 < i < len(summaries) - 1]
    conc_rows = []
    for i, s in interior:
        if not s["n"]:
            continue
        if control_valid:
            expect = p_start + (p_end - p_start) * mids[i]
        else:
            expect = p_start
        expect = min(0.999, max(0.001, expect))
        z = one_prop_z(s["usable"], s["n"], expect)
        delta = round((s["usable_pct"] or 0) - 100.0 * expect, 1)
        conc_rows.append({"workers": s["workers"], "usable_pct": s["usable_pct"],
                          "expected_pct": round(100.0 * expect, 1), "delta_pp": delta,
                          "z": (round(z, 2) if z is not None else None), "p": p_value(z),
                          "significant": bool(z is not None and z < -ALPHA_Z and -delta >= MIN_EFFECT_PP)})
    conc_sig = any(r["significant"] for r in conc_rows)
    out["concurrency"] = {"baseline": "interpolated between control arms" if control_valid else
                          "first arm (no control replicate)", "arms": conc_rows}

    decay_sig = [s["workers"] for s in summaries if s["internal_decay"]["significant"]]
    if decay_sig:
        out["evidence"].append("within-arm decay (early third > late third) at workers=%s"
                               % ",".join(str(w) for w in decay_sig))

    if drift_sig and conc_sig:
        out["hypothesis"] = "both"
    elif drift_sig:
        out["hypothesis"] = "cumulative"
    elif conc_sig:
        out["hypothesis"] = "concurrency"
    elif decay_sig and control_valid:
        # No arm-level signal, but usable% falls inside arms — a quota short enough to be visible within
        # a single arm, and recovered by the time the next arm starts.
        out["hypothesis"] = "cumulative"
        out["evidence"].append("arm averages are flat but usable% falls WITHIN arms — a short-horizon "
                               "quota that recovers between arms")
    else:
        out["hypothesis"] = "no_effect_detected"
        mdd = [r for r in (min_detectable_pp(s["n"], s["n"]) for s in summaries) if r]
        out["caveats"].append(
            "no effect cleared %g pp at 95%%. Smallest difference this sample could detect: ~%s pp. "
            "If the fleet still degrades, the cause is not reproduced by one process at these worker "
            "counts — look at AGGREGATE load across shards, not per-shard concurrency."
            % (MIN_EFFECT_PP, max(mdd) if mdd else "n/a"))

    if drift_sig:
        out["evidence"].append("control replicate at workers=%s fell %s pp between the first and last arm"
                               % (first["workers"], out["control"]["drop_pp"]))
    for r in conc_rows:
        if r["significant"]:
            out["evidence"].append("workers=%s ran %s pp below its time-adjusted baseline"
                                   % (r["workers"], -r["delta_pp"]))

    out["prescription"] = {
        "cumulative": "rotate identities harder — lower the session budget and widen the exit pool. "
                      "More workers is NOT the problem; slowing down only delays the same wall.",
        "concurrency": "pace it — hold total volume and cut requests in flight. More identities buy "
                       "nothing.",
        "both": "pace AND rotate; measure again after each change separately, not together.",
        "no_effect_detected": "do not change the controller on this evidence. Re-run at fleet scale or "
                              "with a larger per-arm n.",
        "inconclusive": "fix the experiment before drawing a conclusion.",
    }.get(out["hypothesis"])
    return out


def analyse(arms):
    summaries = [arm_summary(a) for a in arms]
    agg = by_exit(arms)
    return {"arms": summaries, "verdict": verdict(summaries),
            "by_exit": agg, "exit_pattern": exit_pattern(agg)}


# ── live run ────────────────────────────────────────────────────────────────────────────────────────

class _ProbeTally(object):
    """Wraps the real tally so every request lands in the experiment's record in completion order.

    Classification comes from the fetch path itself (status + body + payload shape), not from re-deriving
    it out here off a None return — the fetch already knows the difference between a closed store and a
    hollowed-out 200, and that distinction is the entire experiment."""

    def __init__(self, inner):
        self.inner = inner
        self.records = []
        self._lock = threading.Lock()

    def record(self, cls, method="direct", exit=None):
        self.inner.record(cls, method=method, exit=exit)
        with self._lock:
            self.records.append((len(self.records), exit, cls))
        return cls

    def __getattr__(self, k):
        return getattr(self.inner, k)


def _run_arm(workers, ids, costume, site, log):
    import blocks
    import getstore
    getstore._PROF[site] = costume
    getstore._TL_RESET[0] += 1            # every thread re-primes on this costume
    inner = blocks.install()
    probe = _ProbeTally(inner)
    blocks._GLOBAL["tally"] = probe

    def _one(url_id):
        su = getstore.url_id_to_uuid(url_id)
        if not su:
            return
        try:
            getstore.fetch_store(su, site=site)
        except Exception:
            pass                          # the tally already recorded the classified failure

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_one, ids))
    el = round(time.time() - t0, 1)
    arm = {"workers": workers, "costume": costume, "elapsed_s": el, "records": list(probe.records)}
    s = arm_summary(arm)
    log("  workers=%-3d n=%-4d %5.1f%% usable  %5.1fs  %.1f req/s  %s"
        % (workers, s["n"], s["usable_pct"] or 0.0, el, s["req_per_s"] or 0.0,
           " ".join("%s=%d" % (k, v) for k, v in sorted(s["classes"].items(), key=lambda x: -x[1])[:4])))
    return arm


def run(plan, per_arm, costumes, site="ubereats", log=print):
    import adapt
    import ue_catalog
    adapt.freeze()                        # nothing may adapt while we measure
    try:
        uni = ue_catalog.universe(site, log=lambda *_: None)
        if not uni:
            log("costume_probe: no universe for %s — nothing to probe" % site)
            return None
        need = per_arm * len(plan) * len(costumes)
        ids = [u for (u, _n) in uni]
        if len(ids) < need:
            log("costume_probe: universe has %d ids, plan needs %d — ids will repeat across arms, which "
                "makes a per-store cache indistinguishable from a healthy response" % (len(ids), need))
        log("costume_probe: %d costume(s) x arms %s x %d requests  (adaptation FROZEN)"
            % (len(costumes), "/".join(str(w) for w in plan), per_arm))
        out = {"site": site, "per_arm": per_arm, "plan": plan, "started": int(time.time()),
               "costumes": {}}
        k = 0
        for costume in costumes:
            log("costume %s:" % costume)
            arms = []
            for w in plan:
                chunk = [ids[(k + i) % len(ids)] for i in range(per_arm)]
                k += per_arm
                arms.append(_run_arm(w, chunk, costume, site, log))
            out["costumes"][costume] = analyse(arms)
            v = out["costumes"][costume]["verdict"]
            log("  -> %s" % v["hypothesis"].upper())
            for e in v["evidence"]:
                log("     %s" % e)
            for c in v["caveats"]:
                log("     (caveat) %s" % c)
            ep = out["costumes"][costume]["exit_pattern"]
            log("     exits: %s (median %s%%, spread %s pp)"
                % (ep["pattern"], ep.get("median_pct"), ep.get("spread_pp")))
        return out
    finally:
        adapt.clear()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="ubereats")
    ap.add_argument("--arms", default="2,8,16,2",
                    help="worker counts, in order. The last SHOULD repeat the first — that replicate is "
                         "the control that separates elapsed time from concurrency.")
    ap.add_argument("--per-arm", type=int, default=120, help="requests per arm (held constant)")
    ap.add_argument("--costumes", default=None, help="comma list of TLS profiles (default: the active one)")
    ap.add_argument("--out", default=None, help="write the full JSON report here")
    a = ap.parse_args(argv)

    plan = [int(x) for x in a.arms.split(",") if x.strip()]
    if len(plan) >= 2 and plan[0] != plan[-1]:
        print("costume_probe: WARNING — the last arm (%d) does not repeat the first (%d). Without that "
              "replicate a decline over time is indistinguishable from a decline with concurrency, and "
              "the verdict will say so rather than guess." % (plan[-1], plan[0]), file=sys.stderr)
    if a.costumes:
        costumes = [c.strip() for c in a.costumes.split(",") if c.strip()]
    else:
        import getstore
        costumes = [getstore._profile(a.site)]

    rep = run(plan, a.per_arm, costumes, site=a.site)
    if rep is None:
        return 1
    if a.out:
        with open(a.out, "w") as f:
            json.dump(rep, f, indent=2)
        print("wrote %s" % a.out)
    else:
        print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
