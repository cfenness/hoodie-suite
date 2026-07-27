#!/usr/bin/env python3
"""aggregator_full_test.py — guard for the aggregator full-detail entries (ubereats-full,
postmates-full, doordash-full, instacart-bevalc).

Deterministic + offline (no network, no connector, no live warehouse): proves each entry keeps the
constraints it was built under — no proxy spend, honest no-creds for Instacart's session gate, and
the new modules import/wire cleanly. Same spirit as dispatch_guard_test (structural, not a
hand-checked list) but scoped to these four rather than the whole registry.

Two shapes coexist here, deliberately:
  - MANUAL-ONLY (ubereats-full, postmates-full, instacart-bevalc): each still carries a real open
    question (is the bare Fly IP viable without a proxy? does a login session actually unlock
    alcohol?) that must be answered by a human-triggered test before it's safe to schedule.
  - RESUMABLE/RECURRING (doordash-full): no open question left to gate on — it converges to full
    national coverage via accumulate-merged batches, same pattern as the already-enabled `naop`, so
    it's enabled like every other converging source. See [[no-silent-caps-in-full-pulls]]: this
    shape (not a manual one-shot capped run) is what "full"/master-data coverage requires.

Run: python3 unifyd/aggregator_full_test.py   (exit 0 = clean, 1 = drift). Also importable as test_*.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MANUAL_ONLY_IDS = {"ubereats-full": "ubereats_products", "postmates-full": "postmates_products",
                   "instacart-bevalc": "instacart_products"}
RECURRING_IDS = {"doordash-full": "doordash_full_runs"}
NEW_IDS = {**MANUAL_ONLY_IDS, **RECURRING_IDS}


def main():
    fails = []
    import source_registry as reg

    by_id = {s["id"]: s for s in reg.SOURCES}
    for sid, table in NEW_IDS.items():
        s = by_id.get(sid)
        if not s:
            fails.append("SOURCES has no '%s' entry" % sid)
            continue
        if sid in MANUAL_ONLY_IDS and s.get("enabled"):
            fails.append("'%s' must be enabled=False (manual trigger only — an open question isn't "
                         "answered yet)" % sid)
        if sid in RECURRING_IDS and not s.get("enabled"):
            fails.append("'%s' should be enabled=True — it's resumable/accumulate-merged, no reason "
                         "to leave national coverage as a manual-only one-off" % sid)
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

    # THE dangerous one: doordash_full.run() must ACCUMULATE, never overwrite. A resumable-batch
    # driver that calls a write_parquet()-based run() would have each batch WIPE the previous
    # batch's landed stores instead of adding to them — the exact silent-data-loss shape this whole
    # redesign exists to prevent. Source-level check (not just "did it not crash") because a
    # regression here wouldn't raise — it would just quietly erase rows.
    try:
        src = open(os.path.join(HERE, "doordash_full.py")).read()
        if "write_parquet(chain" in src or "write_parquet(chain +" in src:
            fails.append("doordash_full.py writes via write_parquet — a resumable batch driver "
                         "would erase the previous batch's rows; must be write_accumulate")
        if "write_accumulate(chain" not in src:
            fails.append("doordash_full.py no longer calls write_accumulate for chain + '_products_full' "
                         "— confirm the accumulate fix wasn't reverted")
    except Exception as e:
        fails.append("could not read doordash_full.py: %s" % e)

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
    print("aggregator full-crawl guard OK — %d manual-only + %d resumable/recurring, all $0"
          % (len(MANUAL_ONLY_IDS), len(RECURRING_IDS)))
    return 0


def test_aggregator_full_bounds():                      # pytest entrypoint
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
