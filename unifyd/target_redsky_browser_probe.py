#!/usr/bin/env python3
"""target_redsky_browser_probe.py — the decisive test: a REAL browser on a datacenter IP, no proxy.

Probe #1 proved curl_cffi from a datacenter IP gets a 403 CAPTCHA (Akamai), not a hard ban — which is
what a real browser clears by running the sensor JS. So: launch real Chrome (headful under Xvfb, the
most trusted posture), navigate target.com so Akamai sets the _abck token, then do the RedSky call
IN-PAGE (carries the browser's cookies + origin — exactly how target.com's own site calls it). No
proxy, no paid anything. If this returns store data, Target is free from the cloud, no Mac.
"""
import sys
import urllib.parse

REDSKY = "https://redsky.target.com/redsky_aggregations/v1/web"
KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"     # public web key (from target_scraper.py)


def _url():
    q = urllib.parse.urlencode({"key": KEY, "limit": 20, "within": 100, "place": "10001", "channel": "WEB"})
    return "%s/nearby_stores_v1?%s" % (REDSKY, q)


def run():
    from playwright.sync_api import sync_playwright
    url = _url()
    with sync_playwright() as p:
        launched = None
        for channel in ("chrome", None):
            try:
                launched = (p.chromium.launch(headless=False, channel=channel, args=["--no-sandbox"])
                            if channel else p.chromium.launch(headless=False, args=["--no-sandbox"]))
                print("launched real browser (channel=%s, headful/Xvfb)" % (channel or "bundled"))
                break
            except Exception as e:
                print("  launch channel=%s failed: %s" % (channel, str(e)[:90]))
        if not launched:
            print("VERDICT: no browser available to test — inconclusive")
            return 2
        b = launched
        ctx = b.new_context(locale="en-US")
        pg = ctx.new_page()
        try:
            pg.goto("https://www.target.com/", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(4000)
            try:
                pg.mouse.move(240, 320); pg.mouse.wheel(0, 900); pg.wait_for_timeout(1500)
                pg.mouse.wheel(0, 700)                        # sensor signals → Akamai posts a clear _abck
            except Exception:
                pass
            pg.wait_for_timeout(5000)
            res = pg.evaluate(
                """async (u) => { try { const r = await fetch(u, {headers:{accept:'application/json'}});
                    return {s:r.status, t:(await r.text()).slice(0,300)}; }
                    catch(e){ return {s:-1, t:String(e)}; } }""", url)
            body = res.get("t", "") or ""
            data = ('"stores"' in body) or ('"nearby_stores"' in body)
            print("  target.com loaded → RedSky in-page fetch: HTTP %s  data=%s" % (res.get("s"), "YES" if data else "NO"))
            print("  body head: %s" % body.replace("\n", " ")[:180])
            b.close()
            if data:
                print("\nVERDICT: FREE CLOUD BROWSER PATH WORKS — real Chrome on a datacenter IP, NO proxy, returned Target data.")
                return 0
        except Exception as e:
            print("  browser run error: %s" % str(e)[:160])
            try:
                b.close()
            except Exception:
                pass
    print("\nVERDICT: even a real browser from this datacenter IP is challenged — grounded; the IP is the issue, "
          "not the method. Next: a cheaper single clean IP, not per-GB residential.")
    return 1


if __name__ == "__main__":
    sys.exit(run())
