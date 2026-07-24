#!/usr/bin/env python3
"""dispatch_guard_test.py — the anti-regression guard.

Scrapers kept "regressing" because the app's /api/run had a PARALLEL set of *_pull functions that drifted from
source_registry (the documented single source of truth): a fix landed in the registry and the app kept running
the old version (walmart -> Bright Data instead of walmart_direct; abc-fws -> 40-sample instead of full crawl;
ubereats/postmates/naop not runnable at all). This test FAILS THE BUILD the moment that drift is reintroduced.

Run: python3 unifyd/dispatch_guard_test.py   (exit 0 = clean, 1 = drift found). Also importable as test_*.
Deterministic + offline: it imports source_registry (light) and server (module-level only, no network).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Sources whose app run-path MUST go through the registry (not a hand-maintained *_pull copy). Add to this list
# whenever a source graduates to the registry; never remove one to "make the test pass".
MUST_ROUTE_VIA_REGISTRY = {"abc-fws", "specs", "binnys", "walmart", "kroger", "total-wine",
                           "ubereats", "postmates", "naop"}


def main():
    os.environ.setdefault("AGENT_NO_AUTH", "1")          # don't require the OIDC gate to import the module
    import source_registry as reg
    import server

    reg_ids = {s["id"] for s in reg.SOURCES}
    conn_map = getattr(server, "_REGISTRY_CONN", {})
    valid = getattr(server, "VALID_CONNS", set())
    legacy = set(getattr(server, "_CONN_PULL", {}))
    fails = []

    # 1. every source that must be registry-routed actually is
    for cid in sorted(MUST_ROUTE_VIA_REGISTRY):
        if cid not in conn_map:
            fails.append("'%s' must route via the registry (_REGISTRY_CONN) but doesn't — drift risk" % cid)

    # 2. every _REGISTRY_CONN target resolves to a real registry id
    for conn, rid in sorted(conn_map.items()):
        if rid not in reg_ids:
            fails.append("_REGISTRY_CONN['%s'] -> '%s' is not a source_registry id" % (conn, rid))
        if conn not in valid:
            fails.append("_REGISTRY_CONN key '%s' is not in VALID_CONNS (unreachable)" % conn)

    # 3. a registry-routed conn must NOT also be wired to a legacy *_pull (ambiguous dispatch = the drift bug)
    for conn in sorted(set(conn_map) & legacy):
        fails.append("'%s' is BOTH registry-routed and in _CONN_PULL — remove the legacy entry" % conn)

    if fails:
        print("DISPATCH GUARD FAILED (%d):" % len(fails))
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("dispatch guard OK — %d conns route through the registry, no drift (%s)"
          % (len(conn_map), ", ".join(sorted(conn_map))))
    return 0


def test_no_dispatch_drift():                            # pytest entrypoint
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
