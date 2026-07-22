#!/usr/bin/env python3
"""instacart_free_probe.py — is the NO-BD, NO-PROXY Instacart path viable? Prove it, don't assume.

The instacart.py recipe uses `bdata browser` (Bright Data, paid) only as the browser DRIVER — the data is
Instacart's own GraphQL (SearchResultsPlacements + a shopId/postalCode/zoneId zone captured from a live
session). "no bd" = drive that with a FREE real browser, no proxy. This probe runs real Chrome on a
datacenter runner, sets a delivery ZIP, reaches a store, and watches for the product GraphQL — reporting
how far the free path gets (and whether Instacart's anti-bot blocks a datacenter browser).
"""
import re
import sys

ZIP = "10001"
GROCERY = ["ALDI", "Publix", "Kroger", "Wegmans", "GIANT", "Food Lion", "Meijer", "Safeway", "Sprouts",
           "Albertsons", "The Fresh Market", "Costco", "CVS"]
BLOCK_HINTS = ["press & hold", "press and hold", "captcha", "are you a robot", "unusual traffic",
               "access denied", "enter the characters", "verify you are human"]


def run():
    from playwright.sync_api import sync_playwright
    gql = []                                              # every graphql request URL we see
    with sync_playwright() as p:
        b = None
        for ch in ("chrome", None):
            try:
                b = (p.chromium.launch(headless=False, channel=ch, args=["--no-sandbox"]) if ch
                     else p.chromium.launch(headless=False, args=["--no-sandbox"]))
                print("launched browser (channel=%s)" % (ch or "bundled")); break
            except Exception as e:
                print("  launch channel=%s failed: %s" % (ch, str(e)[:80]))
        if not b:
            print("VERDICT: no browser — inconclusive"); return 2
        ctx = b.new_context(locale="en-US")
        pg = ctx.new_page()
        pg.on("request", lambda r: gql.append(r.url) if "/graphql" in r.url else None)

        stage = {"home": False, "blocked": False, "zip_set": False, "store": False,
                 "search_gql": False, "products": 0}
        try:
            pg.goto("https://www.instacart.com/", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(5000)
            stage["home"] = True
            body = (pg.content() or "").lower()
            stage["blocked"] = any(h in body for h in BLOCK_HINTS)
            print("homepage loaded; title=%r; anti-bot hint=%s" % (pg.title()[:60], stage["blocked"]))

            # set a delivery ZIP if there's an address box
            for sel in ['input[data-testid="address-input"]', 'input[placeholder*="ZIP"]',
                        'input[placeholder*="address" i]', 'input[type="text"]']:
                try:
                    el = pg.query_selector(sel)
                    if el and el.is_visible():
                        el.click(); el.fill(ZIP); pg.wait_for_timeout(1500)
                        pg.keyboard.press("Enter"); pg.wait_for_timeout(4000)
                        stage["zip_set"] = True
                        print("entered ZIP via %s" % sel); break
                except Exception:
                    pass

            # click into a non-membership grocery
            for name in GROCERY:
                try:
                    link = pg.get_by_text(re.compile(name, re.I)).first
                    if link and link.is_visible():
                        link.click(timeout=5000); pg.wait_for_timeout(6000)
                        stage["store"] = True
                        print("clicked into store: %s -> %s" % (name, pg.url[:70])); break
                except Exception:
                    continue

            # run a search to trigger the product GraphQL
            if stage["store"]:
                try:
                    box = pg.query_selector('input[type="search"], input[placeholder*="Search" i]')
                    if box:
                        box.click(); box.fill("vodka"); pg.keyboard.press("Enter"); pg.wait_for_timeout(6000)
                except Exception:
                    pass

            stage["search_gql"] = any("SearchResultsPlacements" in u or "search" in u.lower() for u in gql)
            # count items rendered on the results grid (rough product signal)
            try:
                stage["products"] = len(pg.query_selector_all('[data-testid*="item" i], li[class*="item" i]'))
            except Exception:
                pass
        except Exception as e:
            print("flow error: %s" % str(e)[:160])
        b.close()

    print("\n--- STAGES ---")
    for k, v in stage.items():
        print("  %-11s %s" % (k, v))
    print("  graphql calls seen: %d  (e.g. %s)" % (len(gql), (gql[0][:90] if gql else "none")))

    reachable = stage["home"] and not stage["blocked"] and (stage["store"] or stage["search_gql"] or len(gql) > 0)
    print("\nVERDICT: %s" % (
        "FREE PATH LOOKS VIABLE — datacenter browser reached Instacart content/GraphQL with NO proxy, NO bd. "
        "Next: capture the zone + replay SearchResultsPlacements (drop `bdata browser`)."
        if reachable else
        ("BLOCKED at the datacenter IP (anti-bot=%s) — grounded; try the residential executor, not bd/paid."
         % stage["blocked"])))
    return 0 if reachable else 1


if __name__ == "__main__":
    sys.exit(run())
