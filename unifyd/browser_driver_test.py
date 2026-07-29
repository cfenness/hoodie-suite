#!/usr/bin/env python3
"""browser_driver_test.py — nothing may import playwright directly. A RATCHET, not a unit test.

The break this locks down, stated precisely so it isn't re-litigated:

  The Fly image installs `patchright` and NOT `playwright`. `unifyd/requirements.txt` has
  `playwright>=1.40` COMMENTED OUT, and the Dockerfile's pip line is
  `pip install -r requirements.txt gunicorn patchright`. Verified live on the running app machine
  (8609e5ceedd258, 2026-07-29): importing playwright raises ModuleNotFoundError, patchright imports OK.

Why it needs a mechanical guard rather than care: every one of these imports is FUNCTION-LOCAL, so a
module with a bad import still imports cleanly, still compiles, still passes its own tests, and still
deploys green. The error appears only when that code path first runs in production. Every check we own
is blind to it. It was fixed once for DoorDash (PR #688) and was then found in SIX more modules —
publix and total-wine among them, both `enabled=True` on a daily cadence.

The two worst shapes, for the record:
  • publix.run() imported it before its per-market try/except -> the whole source hard-failed each tick.
  • total_wine_full.run() calls the importing fn INSIDE a per-batch try/except that logs and continues
    -> every batch failed, and the run reported COMPLETE having landed 0 rows. A quiet degrade is
    indistinguishable from success, which is the repo's #1 silent-failure class.

The fix is one shared resolver, `browser_warm.sync_playwright_api()`. Use it:

    import browser_warm
    sync_playwright = browser_warm.sync_playwright_api()
    with sync_playwright() as p: ...

    python3 unifyd/browser_driver_test.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILED, RAN = [], []

# browser_warm.py is where resolution is ALLOWED to name both drivers: sync_playwright_api() is the
# shared resolver, and Warmer.__enter__ has its own flag-driven pick (it must know WHICH driver it got,
# because patchright takes a different launch branch). Nothing else gets an exemption.
ALLOWED = {"browser_warm.py"}


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def _code_lines(path):
    """Executable lines only. Comments and docstrings legitimately discuss playwright — the point is to
    forbid the IMPORT, not the word. A guard that banned the word would delete the explanations to stay
    green, which is the wrong pressure to put on a comment."""
    src = open(path, encoding="utf-8", errors="replace").read()
    src = re.sub(r'""".*?"""', "", src, flags=re.S)      # drop docstrings/block strings
    src = re.sub(r"'''.*?'''", "", src, flags=re.S)
    return [l for l in src.splitlines() if not l.lstrip().startswith("#")]


def main():
    print("browser driver — one resolver, no direct playwright imports")

    # --- 1. the resolver exists and prefers what the image actually has ---------------------------
    import browser_warm
    check("browser_warm.sync_playwright_api exists", callable(getattr(browser_warm, "sync_playwright_api", None)))
    resolver = _code_lines(os.path.join(HERE, "browser_warm.py"))
    body = "\n".join(resolver)
    check("the resolver tries patchright before playwright",
          body.index("patchright.sync_api") < body.index("playwright.sync_api"))
    check("the resolver falls back rather than hard-failing", "except ImportError" in body)

    # --- 2. NO module imports playwright directly -------------------------------------------------
    # This is the ratchet. A new scraper that reaches for playwright fails here, at $0, instead of in
    # production on its first real run.
    offenders = []
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in ("_archive", "__pycache__", "fixtures", "agent_state")]
        for fn in sorted(files):
            if not fn.endswith(".py") or fn in ALLOWED:
                continue
            path = os.path.join(root, fn)
            for i, line in enumerate(_code_lines(path), 1):
                if re.search(r"^\s*(from\s+playwright[\w.]*\s+import|import\s+playwright\b)", line):
                    offenders.append("%s:%d" % (os.path.relpath(path, HERE), i))
    check("no module imports playwright directly (use browser_warm.sync_playwright_api)",
          not offenders, offenders)

    # --- 3. the ground truth this guard rests on --------------------------------------------------
    # If someone later adds playwright to the image these two checks fail — which is correct: the
    # constraint changed and this file's reasoning should be re-read, not silently outlived.
    req = open(os.path.join(HERE, "requirements.txt"), encoding="utf-8").read()
    live_req = [l for l in req.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    check("playwright is NOT an installed dependency",
          not any(re.match(r"\s*playwright\b", l) for l in live_req))
    dockerfile = os.path.join(os.path.dirname(HERE), "Dockerfile")
    if os.path.exists(dockerfile):
        df = open(dockerfile, encoding="utf-8").read()
        check("the image installs patchright", re.search(r"pip install[^\n]*patchright", df) is not None)

    # --- 4. the modules that were broken now resolve through the shared seam ----------------------
    # Named explicitly: these are the modules the sweep found broken. If one regresses, say which.
    # total_wine_inventory had TWO sites (_pull_bdbrowser and _pull_direct) — hence the count check.
    for mod, sites in (("publix.py", 1), ("total_wine_inventory.py", 2), ("instacart.py", 1),
                       ("menu_site.py", 1), ("off_premise.py", 1), ("doordash_discover.py", 1),
                       ("doordash_geo.py", 1)):
        body = "\n".join(_code_lines(os.path.join(HERE, mod)))
        n = body.count("sync_playwright_api")
        check("%s resolves the driver via the shared seam (%d site/s)" % (mod.replace(".py", ""), sites),
              n >= sites, "found %d, expected >=%d" % (n, sites))

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
