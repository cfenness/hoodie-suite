#!/usr/bin/env python3
"""doordash_geo_test.py — the sweep must be free, runnable off a Mac, and actually cover Texas.

Three things this pins, each of which was broken before the port:

  1. NO BRIGHT DATA. The sweep connected to BD Browser (brd.superproxy.io), the metered per-GB tier
     the repo's free-first rule exists to keep out of default paths. Nothing may reintroduce it.
  2. NO LOCAL-MAC DEPENDENCY. Credentials were read from
     ~/Library/Application Support/brightdata-cli/, a path that exists on exactly one machine — so
     the sweep could not run on Fly at all, which is where everything is supposed to run.
  3. TEXAS IS COVERED. The whole reason to port this: DoorDash's own TX sitemap serves one store, so
     geography is the only route in. A port that works but doesn't reach Houston fixes nothing.

    python3 unifyd/doordash_geo_test.py
"""
import os
import re
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


def main():
    print("doordash_geo — free sweep")
    import doordash_geo as D
    src = open(D.__file__).read()
    # Comments explain WHY the vendor is gone; only executable lines may not mention it.
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))

    # --- 1. the vendor is gone from the code path -------------------------------------------------
    for bad in ("brd.superproxy.io", "connect_over_cdp", "brightdata-cli", "api.brightdata.com"):
        check("no live reference to %s" % bad, bad not in code, bad)
    check("_browser_auth is gone entirely", not hasattr(D, "_browser_auth"))
    # Assert the CALL is gone, not the string — the name survives in a docstring that explains why it
    # was removed, and that prose is worth keeping. A test that forbade the word would delete the
    # explanation to stay green, which is the wrong pressure to put on a comment.
    check("no Proxy.setLocation CALL (a BD-only CDP command)",
          not re.search(r'send\(\s*["\']Proxy\.setLocation', code), "call site still present")

    # --- 2. it can run somewhere other than one Mac -----------------------------------------------
    check("no ~/Library path anywhere", "Library/Application Support" not in code)
    check("launches a local browser instead", hasattr(D, "_launch"))
    # The Fly image ships patchright, NOT playwright — importing playwright directly compiles, tests
    # and deploys clean, then ModuleNotFoundErrors on the first real pin. Pin the driver resolution.
    check("resolves the driver rather than importing playwright directly",
          hasattr(D, "_sync_playwright"))
    check("prefers patchright (what the image actually has)",
          code.index("patchright.sync_api") < code.index("playwright.sync_api"))
    check("pins location via standard CDP", hasattr(D, "_set_location"))
    check("uses Emulation.setGeolocationOverride", "Emulation.setGeolocationOverride" in code)
    # Egress must come from the FLAT-rate ISP pool, never the per-GB rotating tier.
    check("egress uses the flat ISP pool", "isp_url" in code and "isp_enabled" in code)
    check("does not reach for the per-GB tier", "_session_url" not in code and "paygo" not in code)

    # --- 3. Texas is actually reachable ------------------------------------------------------------
    tx = D.MARKET_GROUPS.get("texas") or []
    check("a texas market group exists", len(tx) >= 4, tx)
    for m in ("houston", "dallas", "austin", "sanantonio"):
        check("%s is a market" % m, m in D.MARKETS)
    check("every texas market resolves to a grid",
          all(m in D.MARKETS and len(D.MARKETS[m]) > 0 for m in tx), tx)

    # Houston's grid must actually bracket Houston — a transposed sign or a swapped lat/lon would
    # produce a valid-looking grid somewhere in the Indian Ocean and sweep nothing.
    hou = D.MARKETS["houston"]
    lats = [p[0] for p in hou]
    lons = [p[1] for p in hou]
    check("houston grid brackets 29.76N", min(lats) < 29.76 < max(lats), (min(lats), max(lats)))
    check("houston grid brackets 95.36W", min(lons) < -95.36 < max(lons), (min(lons), max(lons)))
    check("houston grid is dense enough to be a lattice", len(hou) >= 50, len(hou))

    # Spacing must stay under the delivery-zone radius or the lattice leaves holes between pins.
    step = round(sorted({round(abs(a - b), 4) for a, b in zip(lats, lats[1:])} - {0.0})[0], 4)
    check("grid step stays <= 0.07 deg (~4-5 mi, under a delivery radius)", step <= 0.0701, step)

    # --- 4. run_group REPORTS what happened -------------------------------------------------------
    # Two defects lived here, both of the "reported success while doing nothing" class this file
    # exists to catch, and neither could fail a run:
    #   a) run() returns the ROW LIST, so `total += run(...)` raised TypeError on every market that
    #      actually found merchants — caught by the same except and logged as FAILED. A market that
    #      worked read failed; one that found nothing fell through `[] or 0` and read fine. Inverted.
    #   b) with every market failing, it returned 0 and exited clean, so the harness recorded
    #      status='current' delta=0 error='' — a dead sweep indistinguishable from an empty state.
    # Stub run() so this stays offline: no browser, no network, no proxy.
    real_run, quiet = D.run, lambda *a, **k: None
    n_tx = len(D.MARKET_GROUPS["texas"])
    try:
        D.run = lambda m, points=None, log=print: [{"store_id": "%s-%d" % (m, i)} for i in range(3)]
        total = D.run_group("texas", log=quiet)
        check("counts rows across markets instead of adding a list to an int",
              total == 3 * n_tx, "got %r, want %d" % (total, 3 * n_tx))

        D.run = lambda m, points=None, log=print: []
        check("an every-market-empty sweep still returns 0 without raising",
              D.run_group("texas", log=quiet) == 0)

        def boom(m, points=None, log=print):
            raise RuntimeError("proxy auth failed at %s" % m)

        D.run = boom
        raised = None
        try:
            D.run_group("texas", log=quiet)
        except Exception as e:                                    # noqa: BLE001
            raised = e
        check("an every-market-FAILED sweep raises instead of exiting clean", raised is not None,
              "returned normally — the harness would record status='current' delta=0 error=''")
        check("the raised error carries a real per-market reason",
              raised is not None and "proxy auth failed" in str(raised), str(raised)[:120])

        # One bad metro must still not abort the other four — that part of the intent was right.
        def one_bad(m, points=None, log=print):
            if m == D.MARKET_GROUPS["texas"][0]:
                raise RuntimeError("nope")
            return [{"store_id": m}]

        D.run = one_bad
        check("a partial failure does NOT raise and counts the survivors",
              D.run_group("texas", log=quiet) == n_tx - 1)
    finally:
        D.run = real_run

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
