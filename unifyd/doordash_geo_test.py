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
    # Resolution is shared now (browser_warm.sync_playwright_api) — six other modules had the identical
    # break, so a per-module copy was itself the hazard. Assert delegation, not a local try/except: the
    # ordering check this replaced read the literal import strings, which no longer appear here at all.
    check("delegates to the one shared resolver", "sync_playwright_api" in code)
    check("no direct playwright import left in the code path",
          "from playwright" not in code and "import playwright" not in code)
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

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
