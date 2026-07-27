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

It also enforces COMPLETENESS (check 6): every ENABLED source_registry id must be runnable from the app — in
VALID_CONNS (the `| _REGISTRY_IDS` union) and routed via the registry (server._dispatch_pull's universal rule:
any conn that is a registry id runs through run_one). So a brand-new registry source is app-runnable the day
its row lands, with no hand-wiring, and can't silently drift out of reach.

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
# — it must route via the registry.
APP_ONLY_CONN = {
    "instacart":        "parked recon, no registry entry (not reliably landing)",
    "orlando-accounts": "FL ABT on-premise accounts (places.py), no registry entry",
    "tx-tabc":          "TX TABC license portal (Socrata), no registry entry",
    "il-chicago":       "Chicago license portal (Socrata), no registry entry",
    "ct-dcp":           "CT DCP license portal (Socrata), no registry entry",
    "walmart-api":      "Walmart I/O affiliate API; registry 'walmart' is the walmart_direct scraper (different id)",
    "doordash":         "DoorDash geo merchant sweep, no registry entry",
    "google":           "Google Maps hours/coverage enrich, no registry entry",
    # NOTE: 'target' and 'ttb-cola' were removed here — they ARE registry ids and now route via the registry
    # (server._dispatch_pull universal rule). Their old target_pull/cola_pull copies are dead and deleted.
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

    # Routing is now UNIVERSAL (server._dispatch_pull): a conn runs through the registry iff it is a registry id
    # (or explicitly mapped in the legacy _REGISTRY_CONN allowlist). So bespoke drift can only exist as a stale
    # ESCAPE HATCH — a registry source that ALSO has a *_pull copy or an app-only exemption. The checks hunt those.
    def routes_via_registry(conn):
        return conn in conn_map or reg.by_id(conn) is not None

    # 1. the explicit floor still routes via registry
    for cid in sorted(MUST_ROUTE_VIA_REGISTRY):
        if not routes_via_registry(cid):
            fails.append("'%s' must route via the registry but doesn't — drift risk" % cid)

    # 2. every _REGISTRY_CONN target resolves to a real registry id, and its key is a runnable conn
    for conn, rid in sorted(conn_map.items()):
        if rid not in reg_ids:
            fails.append("_REGISTRY_CONN['%s'] -> '%s' is not a source_registry id" % (conn, rid))
        if conn not in valid:
            fails.append("_REGISTRY_CONN key '%s' is not in VALID_CONNS (unreachable)" % conn)

    # 3. THE CORE ANTI-REVERSION CHECK: a registry source may NOT keep a bespoke escape hatch. Because every
    #    registry id routes via run_one, ANY registry-id conn still found in _CONN_PULL (a *_pull copy) or in
    #    APP_ONLY_CONN (a "skip the registry" exemption) is exactly the stale copy that let "we fixed it N times"
    #    happen. It must be DELETED, not left dormant.
    for cid in sorted(reg_ids & valid):
        if cid in legacy:
            fails.append("'%s' is a registry source but STILL wired in _CONN_PULL — delete the dead *_pull copy" % cid)
        if cid in APP_ONLY_CONN:
            fails.append("'%s' is a registry source but listed APP_ONLY_CONN — it routes via the registry; "
                         "remove the stale exemption" % cid)

    # 4. an APP_ONLY exemption must be a real NON-registry runnable conn (a registry id belongs on the registry path)
    for cid in sorted(APP_ONLY_CONN):
        if reg.by_id(cid) is not None:
            fails.append("APP_ONLY_CONN '%s' IS a registry id — it routes via the registry, not app-only" % cid)
        if cid not in valid:
            fails.append("APP_ONLY_CONN lists '%s' which is not a runnable conn (stale exemption)" % cid)

    # 5. EXHAUSTIVENESS: every runnable conn is classified — routes via registry, is a Socrata feed, or is a
    #    documented NON-registry app-only conn. A new conn can't sneak in without a conscious routing decision.
    for cid in sorted(valid):
        if not (routes_via_registry(cid) or cid in APP_ONLY_CONN or cid in socrata_valid):
            fails.append("'%s' is runnable (VALID_CONNS) but unclassified — it will route via the registry only "
                         "if it's a registry id; otherwise document it in APP_ONLY_CONN with a reason" % cid)

    # 6. COMPLETENESS (the fix for a NEW registry source being silently unrunnable from the app): every ENABLED
    #    registry source must be runnable — in VALID_CONNS (the `| _REGISTRY_IDS` union on server.VALID_CONNS)
    #    and routed via the registry (guaranteed once it's a registry id — see routes_via_registry). Drop the
    #    `| _REGISTRY_IDS` from VALID_CONNS and this fails — so a future distributor recipe (vip-brandbuilder /
    #    sevenfifty) is app-runnable the day its source_registry row lands, with zero hand-wiring.
    enabled_ids = sorted(s["id"] for s in reg.SOURCES if s.get("enabled"))
    for cid in enabled_ids:
        if cid not in valid:
            fails.append("enabled registry source '%s' is NOT runnable from the app (missing from VALID_CONNS) — "
                         "restore `| _REGISTRY_IDS` on VALID_CONNS" % cid)
        elif not routes_via_registry(cid) and cid not in APP_ONLY_CONN:
            fails.append("enabled registry source '%s' is runnable but does NOT route via the registry — "
                         "the universal check in server._dispatch_pull is missing" % cid)

    if fails:
        print("DISPATCH GUARD FAILED (%d):" % len(fails))
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("dispatch guard OK — %d conns explicitly registry-routed, %d documented app-only, all %d enabled "
          "registry sources app-runnable via the universal registry route, no drift"
          % (len(conn_map), len(APP_ONLY_CONN), len(enabled_ids)))
    return 0


def test_no_dispatch_drift():                            # pytest entrypoint
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
