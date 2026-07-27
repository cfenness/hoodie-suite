#!/usr/bin/env python3
"""aggregator_full_test.py — guard for the bounded, manual-trigger aggregator full-detail entries
(ubereats-full, postmates-full, doordash-full, instacart-bevalc).

Deterministic + offline (no network, no connector, no live warehouse): proves each entry keeps the
constraints it was built under — no proxy spend, no accidental auto-scheduling, honest no-creds for
Instacart's session gate, and the new modules import/wire cleanly. Same spirit as dispatch_guard_test
(structural, not a hand-checked list) but scoped to these four rather than the whole registry.

Run: python3 unifyd/aggregator_full_test.py   (exit 0 = clean, 1 = drift). Also importable as test_*.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

NEW_IDS = {"ubereats-full": "ubereats_products", "postmates-full": "postmates_products",
          "doordash-full": "doordash_full_runs", "instacart-bevalc": "instacart_products"}


def main():
    fails = []
    import source_registry as reg

    by_id = {s["id"]: s for s in reg.SOURCES}
    for sid, table in NEW_IDS.items():
        s = by_id.get(sid)
        if not s:
            fails.append("SOURCES has no '%s' entry" % sid)
            continue
        # manual-trigger only: none of these may join the automatic hourly scan. This is the whole
        # point of the "no multi-day runs" bound — a run that quietly re-triggers itself daily is
        # exactly the open-ended crawl these were built to NOT be.
        if s.get("enabled"):
            fails.append("'%s' must be enabled=False (manual trigger only)" % sid)
        if table not in (s.get("tables") or []):
            fails.append("'%s' must verify-land '%s' (got %r)" % (sid, table, s.get("tables")))
        code = s.get("code", "")
        # no metered proxy tier, ever — resi._session_url/geo_session_url are the per-GB path;
        # UE_PROXY=1 is specifically what routes ue_crawl.py's crawl_zones/crawl_stores there.
        if "UE_PROXY" in code or "_session_url" in code or "geo_session_url" in code:
            fails.append("'%s' code references the metered proxy path — never spend" % sid)

    ue = by_id.get("ubereats-full") or {}
    if "RESI_ISP_ONLY" not in ue.get("code", ""):
        fails.append("ubereats-full must force RESI_ISP_ONLY=1 (defense-in-depth against metered spend)")
    pm = by_id.get("postmates-full") or {}
    if "RESI_ISP_ONLY" not in pm.get("code", ""):
        fails.append("postmates-full must force RESI_ISP_ONLY=1 (defense-in-depth against metered spend)")

    ic = by_id.get("instacart-bevalc") or {}
    if "INSTACART_SESSION_COOKIES" not in (ic.get("requires") or []):
        fails.append("instacart-bevalc must requires=[INSTACART_SESSION_COOKIES] — without it a run "
                     "before the secret exists must report no-creds, not attempt/fail anonymously")

    try:
        import doordash_chains
        if not callable(getattr(doordash_chains, "run", None)):
            fails.append("doordash_chains.run missing — the registry code string calls m.run()")
    except Exception as e:
        fails.append("import doordash_chains failed: %s" % e)

    try:
        import instacart
        src = open(os.path.join(HERE, "instacart.py")).read()
        if "INSTACART_SESSION_COOKIES" not in src:
            fails.append("instacart.py no longer reads INSTACART_SESSION_COOKIES — cookie injection removed?")
    except Exception as e:
        fails.append("import instacart failed: %s" % e)

    if fails:
        print("AGGREGATOR FULL-CRAWL GUARD FAILED (%d):" % len(fails))
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("aggregator full-crawl guard OK — %d entries bounded, manual-only, $0" % len(NEW_IDS))
    return 0


def test_aggregator_full_bounds():                      # pytest entrypoint
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
