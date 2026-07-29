#!/usr/bin/env python3
"""doordash_sitemap_test.py — the plausibility guard. stdlib only, no network, no warehouse.

Written against a measured live failure: DoorDash's own `sitemap-doordash-tx-stores.xml` serves a stub
containing ONE store, while California's carries 103,811. We harvested the 1, landed it, and reported
success — so Texas sat at a single outlet and a Houston locator search returned nothing from DoorDash,
which read as a fact about Houston rather than a broken feed.

The guard is verdict logic over per-state counts, so it is tested directly on those counts rather than
by faking HTTP. The one assertion that matters: a Texas-shaped harvest can never come back `success`.

    python3 unifyd/doordash_sitemap_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def verdict(per_state, prev=None, fetch_failed=(), cap=None):
    """Mirror of harvest()'s verdict block, driven by counts alone.

    Kept in step with the module by importing its real constants — if THIN_FLOOR or the US set moves,
    this moves with it rather than quietly testing a stale rule.
    """
    import doordash_sitemap as d
    prev = prev or {}
    warnings = []
    if fetch_failed:
        warnings.append("fetch failed: %s" % ", ".join(sorted(set(fetch_failed))))
    thin = sorted(st for st, n in per_state.items()
                  if st in d._US and n < d.THIN_FLOOR and not (cap and n >= cap))
    if thin:
        warnings.append("below floor: %s" % ", ".join("%s=%d" % (s, per_state[s]) for s in thin))
    collapsed = sorted(st for st, n in per_state.items()
                       if prev.get(st, 0) >= d.THIN_FLOOR and n < d.REGRESSION_PCT * prev[st])
    if collapsed:
        warnings.append("collapsed: %s" % ", ".join(collapsed))
    return {"status": "degraded" if warnings else "success", "warnings": warnings}


def main():
    print("doordash_sitemap plausibility guard")
    import doordash_sitemap as d

    HEALTHY = {"CA": 103811, "FL": 38881, "NY": 33335, "VT": 747, "WY": 938, "AK": 957}

    # --- the regression that was actually missed ------------------------------------------------
    tx = dict(HEALTHY, TX=1)
    v = verdict(tx)
    check("a 1-store Texas is DEGRADED, never success", v["status"] == "degraded", v)
    check("the warning names Texas", "TX=1" in " ".join(v["warnings"]), v)

    # --- healthy runs stay quiet (a guard that always fires gets ignored) ------------------------
    v = verdict(HEALTHY)
    check("a healthy national harvest is success", v["status"] == "success", v)
    check("healthy harvest emits no warnings", v["warnings"] == [], v)

    # The genuinely thinnest US states measured live must NOT trip the floor, or every run is
    # degraded and the signal is worthless.
    for st, n in (("VT", 747), ("WY", 938), ("AK", 957)):
        check("%s=%d is above the floor" % (st, n), verdict({st: n})["status"] == "success")

    # --- Canadian territories must not be judged by the US floor --------------------------------
    v = verdict({"YT": 62, "PE": 212, "NT": 300, "CA": 103811})
    check("small Canadian territories don't trip the US floor", v["status"] == "success", v)
    check("YT is not in the US set", "YT" not in d._US)
    check("TX is in the US set", "TX" in d._US)

    # --- collapse vs what we already hold --------------------------------------------------------
    v = verdict({"FL": 900}, prev={"FL": 38881})
    check("a state collapsing 38881 -> 900 is degraded", v["status"] == "degraded", v)
    v = verdict({"FL": 30000}, prev={"FL": 38881})
    check("a normal fluctuation is not a collapse", v["status"] == "success", v)
    # A state we hold nothing for cannot "collapse" — only the floor applies, so no double-report.
    v = verdict({"CA": 103811}, prev={})
    check("no baseline means no collapse warning", v["status"] == "success", v)

    # --- a skipped state is reported, not swallowed ----------------------------------------------
    v = verdict(HEALTHY, fetch_failed=["TX"])
    check("a failed sub-sitemap fetch degrades the run", v["status"] == "degraded", v)
    check("the failed region is named", "TX" in " ".join(v["warnings"]), v)

    # --- a deliberate smoke cap must not masquerade as a broken feed -----------------------------
    v = verdict({"CA": 50, "FL": 50}, cap=50)
    check("--cap smoke run doesn't false-alarm", v["status"] == "success", v)

    check("floor sits below the thinnest real state (747)", d.THIN_FLOOR < 747)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
