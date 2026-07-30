"""publix.py — Publix WEEKLY AD (BOGO) capture. Publix's own ad, NOT the Instacart-gated side, NOT age-gated.

WORKING (2026-07-10): the weekly-ad API is a flyer/GraphQL maze whose XHRs carry headers/persisted-queries
that fight replay, so instead we read the RENDERED deals off publix.com/savings/weekly-ad/view-all — which
lists every deal with its BOGO/savings text. Access via the BD Browser API over CDP (defeats Akamai), with
CDP Proxy.setLocation pinned to a Publix-footprint city so a real store auto-selects (Publix operates in
FL/GA/AL/SC/NC/TN/VA/KY — a non-footprint exit IP yields no store and an empty ad). Lands as publix_products
+ retail_observations (BOGO drives huge volume). Store #331 = Kirkman Oaks, Orlando.
"""
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

# Publix-footprint markets (lat, lon, label) — includes FL, GA, and SC. Each pins the geo so a real store
# auto-selects; widen freely. Kirkman/Conroy Orlando + Columbia/Charleston SC + Atlanta GA to start.
MARKETS = [
    (28.484, -81.462, "Orlando FL (Kirkman/Conroy)"),
    (25.775, -80.194, "Miami FL"),
    (34.001, -81.035, "Columbia SC"),
    (32.777, -79.931, "Charleston SC"),
    (33.749, -84.388, "Atlanta GA"),
]
VIEW_ALL = "https://www.publix.com/savings/weekly-ad/view-all"


def _browser_auth():
    """wss auth for the BD Browser API (cli_browser zone), from the stored/env Bright Data key.
    Publix needs BD Browser unless an ISP pool is configured (see run()), so a missing key is a
    legitimate can't-run — but raise a CLEAR error naming the fix, not a FileNotFoundError on a
    hardcoded macOS cred path (which is what a keyless Linux CI runner hit)."""
    import brightdata
    key = brightdata._key()      # BRIGHTDATA_API_KEY env, else the `bdata login` store (all platforms)
    if not key:
        raise RuntimeError("Publix needs a Bright Data key (set BRIGHTDATA_API_KEY or run `bdata login`) "
                           "or an ISP pool (resi.isp_enabled) — none configured")
    r = urllib.request.Request("https://api.brightdata.com/zone/passwords?zone=cli_browser",
                               headers={"Authorization": "Bearer " + key})
    pw = json.loads(urllib.request.urlopen(r, timeout=30).read())["passwords"][0]
    return "brd-customer-hl_32bcfbaa-zone-cli_browser:%s" % pw


def parse_tile(text):
    """A rendered deal tile -> a row, or None if it's not a product deal. Handles the two Publix formats:
    '<name> buy 1 get 1 free ... save up to $X.XX'  and  '<name> Save $X.XX on ...'."""
    t = re.sub(r"\s+", " ", text).strip()
    bogo = bool(re.search(r"buy 1 get 1 free|BOGO", t, re.I))
    m = re.search(r"(.+?)\s+buy 1 get 1 free", t, re.I) or re.search(r"(.+?)\s+Save \$", t)
    name = (m.group(1).strip() if m else "").strip()
    if not name or len(name) > 90 or name.lower() in ("weekly ad", "bogo"):
        return None
    sv = re.search(r"save up to \$([\d.]+)|Save \$([\d.]+)", t, re.I)
    savings = float(next(g for g in sv.groups() if g)) if sv else None
    return dict(name=name, promo_type="BOGO" if bogo else "sale", is_bogo=bogo,
                savings=savings, deal_text=t[:160], is_hemp=observe.is_hemp(name))


def _pull_market(pg, cdp, lat, lon, label):
    # BD path pins the store via CDP Proxy.setLocation; the ISP path pins it via the browser context's
    # geolocation (set at launch), so cdp is None there and we skip this.
    if cdp is not None:
        cdp.send("Proxy.setLocation", {"lat": lat, "lon": lon, "distance": 40, "strict": True})
    pg.goto(VIEW_ALL, wait_until="domcontentloaded", timeout=90000)
    time.sleep(9)
    for _ in range(9):
        pg.mouse.wheel(0, 3000); time.sleep(1.8)
    tiles = pg.evaluate("""() => {
      const out=new Set();
      document.querySelectorAll('a,article,li,div').forEach(e=>{
        const t=(e.innerText||'').trim().replace(/\\s+/g,' ');
        if(t && /buy 1 get 1 free|BOGO|save up to \\$|Save \\$/i.test(t) && t.length<160) out.add(t);
      });
      return [...out];
    }""")
    store = ""
    try:
        head = pg.inner_text("body")[:600]
        sm = re.search(r"([A-Za-z .'-]+,\s*[A-Z]{2}\s*\d{5})", head)
        store = sm.group(1) if sm else label
    except Exception:
        store = label
    return tiles, store


def run(markets=None, browser_auth=None, use_isp=None):
    """Weekly-ad deals per market. Two transports, same extraction:
      • ISP (default when an ISP pool is configured): a LOCAL Chrome through a US ISP IP, store pinned by the
        browser context's geolocation — flat-rate, replaces BD Browser. $0 marginal per market.
      • BD Browser (fallback): remote CDP + Proxy.setLocation (metered)."""
    import resi
    import browser_warm
    sync_playwright = browser_warm.sync_playwright_api()   # patchright on the image; NEVER import playwright
    markets = markets or MARKETS
    isp_on = resi.isp_enabled() if use_isp is None else use_isp
    auth = None if isp_on else (browser_auth or _browser_auth())
    rows, seen = [], set()
    with sync_playwright() as p:
        for lat, lon, label in markets:
            try:
                if isp_on:
                    # local browser through a US ISP IP; geolocation set at context = store auto-selects
                    import browser_warm
                    px = resi.isp_us_url("publix-%s" % label)
                    b = p.chromium.launch(headless=True, channel="chrome", proxy=browser_warm._parse_proxy(px),
                                          args=["--disable-blink-features=AutomationControlled", "--no-first-run"])
                    ctx = b.new_context(user_agent=browser_warm.UA, locale="en-US",
                                        viewport={"width": 1300, "height": 1400},
                                        geolocation={"latitude": lat, "longitude": lon}, permissions=["geolocation"])
                    pg = ctx.new_page()
                    tiles, store = _pull_market(pg, None, lat, lon, label)
                    b.close()
                else:
                    # CDP Proxy.setLocation can only be set ONCE per browser session, so reconnect per market
                    b = p.chromium.connect_over_cdp("wss://%s@brd.superproxy.io:9222" % auth, timeout=60000)
                    ctx = b.contexts[0] if b.contexts else b.new_context()
                    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                    cdp = ctx.new_cdp_session(pg)
                    tiles, store = _pull_market(pg, cdp, lat, lon, label)
                    b.close()
            except Exception as e:
                print("  [publix] %s failed: %s" % (label, str(e)[:80])); continue
            n = 0
            for t in tiles:
                r = parse_tile(t)
                if not r:
                    continue
                k = (store, r["name"])
                if k in seen:
                    continue
                seen.add(k); r.update(store=store, market=label, source="publix"); rows.append(r); n += 1
            print("  [publix] %-28s (%s) — %d deals" % (label, store, n))
    if rows:
        # ACCUMULATE — a partial-market run() must not wipe other markets' weekly-ad rows.
        warehouse.write_accumulate("publix_products", rows,
                                   key=lambda r: (r.get("market"), r.get("store"), r.get("name")))
        observe.record("publix", [dict(store=r["store"], product_id="", name=r["name"], brand="",
                                        price=r.get("savings"), on_promo=True, in_stock=True,
                                        is_hemp=r.get("is_hemp")) for r in rows])
        print("[publix] DONE %d weekly-ad deals across %d markets -> warehouse" %
              (len(rows), len({r["market"] for r in rows})))
    return len(rows)


if __name__ == "__main__":
    run()
