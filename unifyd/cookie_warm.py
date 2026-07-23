#!/usr/bin/env python3
"""cookie_warm.py — mint an anti-bot cookie with a real headful browser, hand it to the scraper.

Some sources (Kroger = Akamai, Total Wine = PerimeterX, Albertsons = Kasada, Ahold = DataDome) gate their
first-party API on a cookie that only a REAL browser can produce (the anti-bot JS runs, POSTs a sensor
payload, and validates the token). curl_cffi can't run that JS, so a headless request tarpits/403s. This
warms the cookie once in headful Chrome (on a box that has Chrome+Xvfb — the ephemeral pull machine or the
Mac), through the residential proxy, then the scraper replays the API with `Cookie: <warmed>`.

Used as the registry `cookie` PREP: run_sources.run_one warms it and exports the env var BEFORE the pull
subprocess, so e.g. kroger_atlas reads a fresh KROGER_COOKIE without ever hard-coding one.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def warm(host, path="/", settle_ms=4500, human_moves=4, log=print):
    """Load https://<host><path> in headful Chrome (through the residential proxy), let the site's anti-bot
    JS mint + validate its cookie, and return the full `Cookie:` header string (or '' on failure)."""
    import browser_warm
    try:
        import resi
        proxy = resi.browser()
    except Exception:
        proxy = None
    try:
        with browser_warm.Warmer(host, channel="chrome", headful=True, proxy=proxy) as w:
            p = w._page()
            w.prime("https://%s%s" % (host, path), settle_ms=settle_ms)
            # a real render + a little human motion is what makes Akamai/PX flip the token from pending→valid
            try:
                w.human(human_moves)
            except Exception:
                pass
            p.wait_for_timeout(settle_ms)
            cookies = p.context.cookies()
            ck = "; ".join("%s=%s" % (c["name"], c["value"]) for c in cookies if c.get("name"))
            log("[cookie_warm] %s → %d cookies (%d chars)" % (host, len(cookies), len(ck)))
            return ck
    except Exception as e:
        log("[cookie_warm] %s FAILED: %s" % (host, str(e)[:140]))
        return ""


def apply_prep(source, log=print):
    """Run a registry source's `cookie` prep: warm the cookie, export its env var (+ any static env like a
    store/facility id) into os.environ so the pull subprocess inherits them. Returns True if a cookie landed."""
    c = source.get("cookie")
    if not c:
        return True                                    # no prep needed
    ck = warm(c["host"], path=c.get("path", "/"), log=log)
    for k, v in (c.get("static") or {}).items():       # e.g. KROGER_STORE / KROGER_FACILITY defaults
        os.environ.setdefault(k, str(v))
    if ck:
        os.environ[c["env"]] = ck
        return True
    log("[cookie_warm] %s: no cookie for %s — the pull will degrade/no-creds" % (source.get("id"), c["host"]))
    return False


if __name__ == "__main__":                             # quick manual check: python cookie_warm.py www.kroger.com
    h = sys.argv[1] if len(sys.argv) > 1 else "www.kroger.com"
    print(warm(h)[:400])
