"""instacart.py — Instacart connector on the aggregator harness (see aggregator.py).

STATUS (2026-07-10): API cracked + anti-bot solved; NOT yet reliably landing data — the last mile is
BD-browser automation hardening (see FRICTION below). Recon proved every hard unknown:

  • Anti-bot: `bdata browser` (Bright Data Browser API, zone cli_browser) defeats Forter/reCAPTCHA where
    local headless can't. Open with `--country us`; a delivery ZONE auto-sets from the residential IP
    (no address dance) — the homepage then lists real retailers with delivery times.
  • Product API: persisted GraphQL
      GET https://www.instacart.com/graphql?operationName=SearchResultsPlacements
          &variables={query, shopId, postalCode, zoneId, first, orderBy:"bestMatch", searchSource:"search",
                      filters:[], contentManagementSearchParams:{itemGridColumnCount:N}, pageViewId, ...}
          &extensions={persistedQuery:{version:1, sha256Hash:SEARCH_HASH}}
    Returns clean JSON at data.searchResultsPlacements.placements[]. Confirmed it returns JSON (a spirits
    query returned the alcohol-gate payload, proving the call works).
  • shopId is per-retailer-per-zone; zone = {postalCode, zoneId} from the IP. Get all three from any live
    SearchResultsPlacements request URL in `bdata browser network` (they're in the variables).
  • ALCOHOL GATE (verified in PA and LA): alcohol needs a logged-in, age-verified session — anonymous
    returns "alcohol products aren't available" + "Log in". NON-alcohol browses anonymously. So bev-alc
    needs an account session (cookies) injected into the BD browser; non-alcohol is the proof-of-pipe.

FRICTION still to harden (why this doesn't land data yet):
  • Membership retailers (Costco/Sam's) show a membership wall → no SearchResultsPlacements fires. Pick a
    regular grocery (ALDI, Target, Publix, Kroger, Rouses, …).
  • Storefronts must be ENTERED (click the retailer) before /store/<slug>/s?k= works; a direct search URL
    without shop context redirects to the homepage.
  • Params must be scraped from a LIVE SearchResultsPlacements request (only present once a real search
    fires) — capture that URL, then re-issue with your own query terms.
  • Direct navigation to a raw graphql URL is occasionally flaky ("site can't be reached") — retry.

Once those are hardened this class's hooks drop straight onto AggregatorConnector; DoorDash/Uber Eats then
reuse the same base (their own persisted op + zone + login).
"""
import json
import re
import subprocess
import time
import urllib.parse

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aggregator import AggregatorConnector

GRAPHQL = "https://www.instacart.com/graphql"
SEARCH_OP = "SearchResultsPlacements"
SEARCH_HASH = "6f8d4a3f450d8d25dbb87b6b5bcb82180a1b3c972366fb1fb7de816c05523f4a"
# non-membership groceries to prefer (membership warehouses block the product query)
GROCERY = ["ALDI", "Target", "Publix", "Kroger", "Rouses", "Wegmans", "GIANT", "Food Lion",
           "Meijer", "Safeway", "Albertsons", "The Fresh Market", "Sprouts"]


def _bb(*args, timeout=120):
    """Run a `bdata browser` subcommand, return stdout."""
    return subprocess.run(["bdata", "browser", *args], capture_output=True, text=True, timeout=timeout).stdout


class Instacart(AggregatorConnector):
    source = "instacart"

    def __init__(self, session="ic"):
        self.session = session

    def open_session(self, address=None):
        _bb("open", "https://www.instacart.com/", "--country", "us", "--session", self.session)
        return self.session

    def open_zone(self, session, address=None):
        """Enter a regular (non-membership) grocery so shop context is set, run one search to capture a
        live SearchResultsPlacements URL, and read {shopId, postalCode, zoneId} + slug back out."""
        snap = _bb("snapshot", "--interactive", "--session", session)
        ref = slug = ""
        for name in GROCERY:
            m = re.search(r'link "([^"]*%s[^"]*)" \[ref=(e\d+)\]' % re.escape(name), snap, re.I)
            if m:
                ref = m.group(2); break
        if not ref:
            raise RuntimeError("no non-membership grocery retailer found on the homepage")
        _bb("click", ref, "--session", session); time.sleep(4)
        st = _bb("status", "--session", session)
        sm = re.search(r"/store/([^/\"]+)", st)
        slug = sm.group(1) if sm else ""
        _bb("open", "https://www.instacart.com/store/%s/s?k=milk" % slug, "--country", "us", "--session", session)
        time.sleep(6)
        url = ""
        for line in _bb("network", "--session", session).splitlines():
            if SEARCH_OP in line:
                m = re.search(r"https://www\.instacart\.com/graphql\?operationName=%s[^\s]+" % SEARCH_OP, line)
                if m:
                    url = m.group(0); break
        if not url:
            raise RuntimeError("no %s call captured for slug %s (membership wall?)" % (SEARCH_OP, slug))
        v = json.loads(urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["variables"][0]))
        return {"slug": slug, "shopId": v["shopId"], "postalCode": v["postalCode"], "zoneId": v["zoneId"]}

    def _search_url(self, zone, query, first=24):
        variables = {"query": query, "shopId": zone["shopId"], "postalCode": zone["postalCode"],
                     "zoneId": zone["zoneId"], "first": first, "orderBy": "bestMatch", "searchSource": "search",
                     "filters": [], "disableReformulation": False, "disableLlm": False, "forceInspiration": False,
                     "clusterId": None, "action": None, "elevatedProductId": None,
                     "pageViewId": "00000000-0000-0000-0000-000000000000", "includeDebugInfo": False,
                     "clusteringStrategy": None, "contentManagementSearchParams": {"itemGridColumnCount": 4}}
        ext = {"persistedQuery": {"version": 1, "sha256Hash": SEARCH_HASH}}
        return "%s?operationName=%s&variables=%s&extensions=%s" % (
            GRAPHQL, SEARCH_OP, urllib.parse.quote(json.dumps(variables, separators=(",", ":"))),
            urllib.parse.quote(json.dumps(ext, separators=(",", ":"))))

    def fetch_page(self, session, zone, query, cursor=None):
        for _ in range(3):                       # raw-graphql nav is occasionally flaky — retry
            _bb("open", self._search_url(zone, query), "--country", "us", "--session", session)
            time.sleep(3)
            txt = _bb("get", "text", "--session", session)
            m = re.search(r"(\{.*\})", txt, re.S)
            if m:
                try:
                    d = json.loads(m.group(1))
                    placements = (((d.get("data") or {}).get("searchResultsPlacements") or {}).get("placements")) or []
                    return placements, None      # SearchResultsPlacements isn't cursor-paged in this op
                except Exception:
                    pass
            time.sleep(2)
        return [], None

    def parse_item(self, raw, retailer, zone):
        """Pull item cards out of a placement. Instacart item nodes are `Item*` typenames carrying a name,
        a price viewSection (priceString/fullPriceString), a size, and an image."""
        def find_items(o, out):
            if isinstance(o, dict):
                if str(o.get("__typename", "")).startswith("Item") and (o.get("name") or o.get("id")):
                    out.append(o)
                for v in o.values():
                    find_items(v, out)
            elif isinstance(o, list):
                for v in o:
                    find_items(v, out)
        items = []
        find_items(raw, items)
        if not items:
            return None
        it = items[0]
        price_str = json.dumps(it)              # price lives in a nested viewSection; pull the first $x.xx
        pm = re.search(r"\$(\d+\.\d{2})", price_str)
        img = re.search(r'https://[^"\\]+?\.(?:png|jpe?g)[^"\\]*', price_str)
        return dict(retailer=zone.get("slug", retailer), store=zone.get("slug", retailer),
                    store_id=zone.get("shopId"), zone=zone.get("zoneId"),
                    product_id=str(it.get("id") or it.get("legacyId") or ""), upc="",
                    brand="", name=it.get("name", ""), category="", size=it.get("size", ""),
                    price=float(pm.group(1)) if pm else None, in_stock=True,
                    image_url=img.group(0) if img else "", url="",
                    raw_json=json.dumps(it)[:4000])
