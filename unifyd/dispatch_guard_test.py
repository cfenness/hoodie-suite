#!/usr/bin/env python3
"""dispatch_guard_test.py — the anti-regression guard.

Scrapers kept "regressing" because the app's /api/run had a PARALLEL set of *_pull functions that drifted from
source_registry (the documented single source of truth): a fix landed in the registry and the app kept running
the old version (walmart -> Bright Data instead of walmart_direct; abc-fws -> 40-sample instead of full crawl;
ab-inbev -> ab_locator instead of ab_fill; ubereats/postmates/naop not runnable at all). This test FAILS THE
BUILD the moment that drift is reintroduced.

It is STRUCTURAL, not a hand-maintained allowlist: it derives the policy from source_registry + the server's
dispatch tables, so a NEW registry-owned source that gets hand-wired in the app is caught automatically. The
only manual input is APP_ONLY_CONN — the set of app connIds that legitimately do NOT map to a registry source
(state license portals, parked recon, affiliate APIs), each with the reason it's exempt. Adding an exemption is
a conscious, reviewed decision; forgetting to classify a new conn fails the test.

Run: python3 unifyd/dispatch_guard_test.py   (exit 0 = clean, 1 = drift found). Also importable as test_*.
Deterministic + offline: imports source_registry (light) and server (module-level only, no network).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Sources whose app run-path MUST go through the registry (not a hand-maintained *_pull copy). This is the
# explicit floor; the structural checks below extend it to every registry-owned conn automatically. Never
# remove one to "make the test pass".
MUST_ROUTE_VIA_REGISTRY = {"abc-fws", "specs", "binnys", "walmart", "kroger", "total-wine",
                           "ubereats", "postmates", "naop", "vtinfo", "ab-inbev"}

# App connIds that intentionally do NOT route through the registry, each with WHY. Two kinds:
#   - app-only sources with NO source_registry entry (state license portals, parked recon, affiliate API);
#   - a conn whose id is NOT a registry id, so there is nothing to drift against.
# A registry-owned source id (one that appears in source_registry AND is runnable by the app) may NOT sit here
# — it must route via the registry. The lone documented exception is target (see below), a real follow-up.
APP_ONLY_CONN = {
    "ttb-cola":         "live COLA runner; registry 'ttb' is the disabled weekly backfill (different id)",
    "instacart":        "parked recon, no registry entry (not reliably landing)",
    "orlando-accounts": "FL ABT on-premise accounts (places.py), no registry entry",
    "census-acs":       "ACS demographics (census.py -> census_acs); registry 'census' is census_ref -> "
                        "census_reference, a DIFFERENT dataset — reconcile before routing via the registry",
    "tx-tabc":          "TX TABC license portal (Socrata), no registry entry",
    "il-chicago":       "Chicago license portal (Socrata), no registry entry",
    "ct-dcp":           "CT DCP license portal (Socrata), no registry entry",
    "walmart-api":      "Walmart I/O affiliate API; registry 'walmart' is the walmart_direct scraper (different id)",
    "target":           "FOLLOW-UP: registry 'target' runs target_scraper.run(); the app path runs the national "
                        "sweep. Reconcile onto the registry, then move target into _REGISTRY_CONN and delete this.",
    "doordash":         "DoorDash geo merchant sweep, no registry entry",
    "google":           "Google Maps hours/coverage enrich, no registry entry",
}


def main():
    os.environ.setdefault("AGENT_NO_AUTH", "1")          # don't require the OIDC gate to import the module
    import source_registry as reg
    import server
    try:
        import socrata_outlets
        socrata_valid = set(socrata_outlets.VALID)
    except Exception:
        socrata_valid = set()

    reg_ids = {s["id"] for s in reg.SOURCES}
    conn_map = getattr(server, "_REGISTRY_CONN", {})
    valid = set(getattr(server, "VALID_CONNS", set()))
    legacy = set(getattr(server, "_CONN_PULL", {}))
    fails = []

    # 1. every source that must be registry-routed actually is
    for cid in sorted(MUST_ROUTE_VIA_REGISTRY):
        if cid not in conn_map:
            fails.append("'%s' must route via the registry (_REGISTRY_CONN) but doesn't — drift risk" % cid)

    # 2. every _REGISTRY_CONN target resolves to a real registry id, and its key is a runnable conn
    for conn, rid in sorted(conn_map.items()):
        if rid not in reg_ids:
            fails.append("_REGISTRY_CONN['%s'] -> '%s' is not a source_registry id" % (conn, rid))
        if conn not in valid:
            fails.append("_REGISTRY_CONN key '%s' is not in VALID_CONNS (unreachable)" % conn)

    # 3. a registry-routed conn must NOT also be wired to a legacy *_pull (ambiguous dispatch = the drift bug)
    for conn in sorted(set(conn_map) & legacy):
        fails.append("'%s' is BOTH registry-routed and in _CONN_PULL — remove the legacy entry" % conn)

    # 4. STRUCTURAL anti-drift (the core check): any conn whose id is a source_registry id, and which the app
    #    can run, MUST route through the registry — unless it's a documented APP_ONLY_CONN exception. This is
    #    what catches the vtinfo/ab-inbev class (a registry-owned source hand-wired in the app else-chain) that
    #    the hand-maintained MUST_ROUTE list silently missed.
    for cid in sorted(valid & reg_ids):
        if cid not in conn_map and cid not in APP_ONLY_CONN:
            fails.append("'%s' is a source_registry id the app can run, but it is NOT routed via the registry "
                         "and NOT a documented app-only exception — dispatch drift (add it to _REGISTRY_CONN)"
                         % cid)

    # 5. an APP_ONLY exception must be real: it must be a runnable conn and must NOT also be registry-routed.
    for cid in sorted(APP_ONLY_CONN):
        if cid in conn_map:
            fails.append("'%s' is in APP_ONLY_CONN yet also registry-routed — remove one" % cid)
        if cid not in valid:
            fails.append("APP_ONLY_CONN lists '%s' which is not a runnable conn (stale exemption)" % cid)

    # 6. EXHAUSTIVENESS: every runnable conn is classified (registry-routed, documented app-only, or a Socrata
    #    outlet feed). A new conn cannot be added to VALID_CONNS without a conscious routing decision.
    classified = set(conn_map) | set(APP_ONLY_CONN) | socrata_valid
    for cid in sorted(valid - classified):
        fails.append("'%s' is runnable (VALID_CONNS) but unclassified — add it to _REGISTRY_CONN (preferred) "
                     "or APP_ONLY_CONN with a reason" % cid)

    if fails:
        print("DISPATCH GUARD FAILED (%d):" % len(fails))
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("dispatch guard OK — %d conns route through the registry, %d documented app-only, no drift"
          % (len(conn_map), len(APP_ONLY_CONN)))
    return 0


def test_no_dispatch_drift():                            # pytest entrypoint
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
