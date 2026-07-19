#!/usr/bin/env python3
"""isp_viability.py — classify which chain sources can move onto the flat-rate ISP pool.

LESSON (learned the hard way): a source's HOMEPAGE passing through ISP does NOT mean its DATA endpoint does —
Target's site loads but RedSky 403s the ISP ASN. So this probes each source's REAL data surface, and gives a
two-tier verdict:

  Tier 1  curl-through-ISP on the data endpoint     → ISP-DIRECT if real data comes back (no cookie needed).
  Tier 2  (--warm) browser-through-ISP, US exit IP  → ISP-WARM if a warmed real-Chrome session renders it.
  neither → BLOCKED (stays on premium residential / BD).

Sources already fetched DIRECT / $0 (abc-fws, 7now) are tagged so — nothing to save by moving them.

  python isp_viability.py            # fast: curl tier only
  python isp_viability.py --warm     # + browser-through-ISP tier for whatever curl couldn't get
"""
import json
import os
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resi

_TGT_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
_TGT = "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2?" + urllib.parse.urlencode(
    {"key": _TGT_KEY, "channel": "WEB", "count": 28, "default_purchasability_filter": "true", "keyword": "red wine",
     "offset": 0, "page": "/s/red wine", "platform": "desktop", "pricing_store_id": "2259", "store_ids": "2259",
     "visitor_id": "0193", "zip": "20001"})

# (name, data_url, cost_path, marker) — cost_path: 'BD'|'direct'|'direct/warm' (what it uses TODAY);
# marker = a string that appears only in REAL data (so a challenge/empty page fails the check).
SOURCES = [
    ("target",    _TGT, "BD", '"__typename"'),                                  # RedSky JSON
    ("totalwine", "https://www.totalwine.com/product/api/product/product-detail/v1/getProduct/223968750", "direct/warm", '"stockLevel"'),
    ("kroger",    "https://www.kroger.com/atlas/v1/product/v2/products?filter.gtin13=0083085202095", "direct", '"productId"'),
    ("7now",      "https://www.7now.com/api/bff/unified-catalog/v1", "direct/warm", '"products"'),
    ("walmart",   "https://www.walmart.com/search?q=vodka", "BD", '"productName"'),
    ("abc-fws",   "https://www.abcfws.com/", "direct", "addtocart"),            # BigCommerce product-serving
    ("publix",    "https://www.publix.com/savings/weekly-ad/view-all", "BD", "bogo"),   # rendered deals only
    ("specs",     "https://specsonline.com/shop/en/specsonline/wine", "direct", "add to cart"),
    ("binnys",    "https://www.binnys.com/wine", "BD", "add to cart"),          # Cloudflare-gated product data
]
BLOCK_MARKERS = ("access denied", "px-captcha", "perimeterx", "_incapsula_", "incapsula",
                 "request unsuccessful", "reference #", "robot or human", "pardon the interruption",
                 "are you a human", "bot detection", "distil", "cf-chl", "just a moment", "forbidden")


def _has(body, marker):
    return (marker or "").lower() in (body or "").lower()


def _blocked(status, body):
    low = (body or "")[:6000].lower()
    m = next((x for x in BLOCK_MARKERS if x in low), None)
    return (status in (401, 403, 407, 429, 503)) or bool(m), m


def curl_tier(name, url, marker):
    from curl_cffi import requests as cr
    for i in range(2):
        px = resi.isp_url("%s%d" % (name, i))
        try:
            r = cr.Session(impersonate="chrome", timeout=30, proxies={"http": px, "https": px}).get(url)
            if r.status_code == 200 and _has(r.text, marker) and len(r.text) > 800:
                return True, "curl: real data (%dKB)" % (len(r.text) // 1024)
            blk, m = _blocked(r.status_code, r.text)
            note = "curl: %s" % (("marker '%s'" % m) if m else "status %s" % r.status_code) if blk else \
                   "curl: 200 but no data marker (%dB)" % len(r.text)
        except Exception as e:
            note = "curl ERR " + str(e)[:34]
    return False, note


def warm_tier(name, url, marker):
    """Browser-through-ISP on a US exit IP — the real test for cookie/JS-gated data endpoints."""
    from curl_cffi import requests as cr
    usurl = None
    for p in resi.isp_pool():
        try:
            d = json.loads(cr.Session(impersonate="chrome", timeout=18,
                           proxies={"http": p, "https": p}).get("https://ipapi.co/json/",
                           headers={"User-Agent": "curl/8"}).text)
            if d.get("country_code") == "US":
                usurl = p; break
        except Exception:
            pass
    if not usurl:
        return False, "warm: no US IP in pool"
    os.environ["BROWSER_PROXY"] = usurl
    import browser_warm
    dom = urllib.parse.urlsplit(url).hostname or name
    try:
        with browser_warm.Warmer(dom, headful=False, channel="chrome", proxy=usurl) as w:
            html = w.prime(url, settle_ms=8000).content()
            blk, m = _blocked(200, html)
            if _has(html, marker) and not blk:
                return True, "warm: rendered (%dKB)" % (len(html) // 1024)
            return False, "warm: %s (%dKB)" % (("blocked '%s'" % m) if m else "no data marker", len(html) // 1024)
    except Exception as e:
        return False, "warm ERR " + str(e)[:34]


def main(do_warm):
    if not resi.isp_enabled():
        print("ISP pool empty — set ISP_PROXIES / isp_proxies.txt first."); return
    print("Probing %d chain DATA endpoints through the ISP pool (%d IPs)%s…\n"
          % (len(SOURCES), len(resi.isp_pool()), " [+warm tier]" if do_warm else ""))
    rows = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(curl_tier, n, u, mk): (n, u, cp, mk) for n, u, cp, mk in SOURCES}
        for f in as_completed(futs):
            n, u, cp, mk = futs[f]
            ok, why = f.result()
            rows.append({"name": n, "cost": cp, "tier": "DIRECT" if ok else None, "why": why, "url": u, "marker": mk})
    # warm tier for the ones curl couldn't get (serialized — each spins a browser)
    if do_warm:
        for r in rows:
            if r["tier"] is None:
                ok, why = warm_tier(r["name"], r["url"], r["marker"])
                r["tier"] = "WARM" if ok else "BLOCKED"
                r["why"] += " | " + why
    for r in sorted(rows, key=lambda x: {"DIRECT": 0, "WARM": 1, None: 2, "BLOCKED": 3}.get(x["tier"], 4)):
        verdict = r["tier"] or "curl-blocked"
        save = "" if r["cost"] != "BD" else "  ← on BD (cost win if viable)"
        print("  %-9s %-11s [today:%-11s] %s%s" % (verdict, r["name"], r["cost"], r["why"], save))
    viable = [r["name"] for r in rows if r["tier"] in ("DIRECT", "WARM")]
    bd_win = [r["name"] for r in rows if r["tier"] in ("DIRECT", "WARM") and r["cost"] == "BD"]
    print("\nISP-VIABLE:", viable or "none")
    print("COST WINS (currently on BD → movable to flat-rate):", bd_win or "none — BD sources block ISP")
    if not do_warm:
        print("(run with --warm to browser-test the curl-blocked ones)")


if __name__ == "__main__":
    import kroger_api
    kroger_api._load_creds()
    main("--warm" in sys.argv)
