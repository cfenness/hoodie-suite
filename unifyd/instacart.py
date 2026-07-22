"""instacart.py — Instacart connector on the aggregator harness (see aggregator.py).

STATUS (2026-07-22): FREE PATH. The browser DRIVER is now a self-hosted Playwright Chromium — NO Bright Data,
NO proxy. A cloud probe (`instacart_free_probe.py`, run on a bare datacenter runner) proved a real Chromium
reaches Instacart's homepage → a grocery storefront → the product GraphQL with no anti-bot block and no paid
layer (home=True blocked=False store=True search_gql=True products=76). The data is Instacart's own persisted
GraphQL — the browser is only how we drive a real session; it never needed to be a paid one.

The recipe (unchanged from the BD era — only the driver changed):
  • ZONE: a delivery zone = {shopId, postalCode, zoneId}. It rides in the `variables` of every live
    `SearchResultsPlacements` request. We ENTER a non-membership grocery storefront (membership warehouses
    wall the product query), run one seed search, and read the three ids back out of the captured request URL.
  • PRODUCT API: persisted GraphQL
      GET https://www.instacart.com/graphql?operationName=SearchResultsPlacements
          &variables={query, shopId, postalCode, zoneId, first, orderBy:"bestMatch", ...}
          &extensions={persistedQuery:{version:1, sha256Hash:SEARCH_HASH}}
    Returns clean JSON at data.searchResultsPlacements.placements[]. We replay it per query term with our own
    zone — no browser interaction per page, just a navigation to the graphql URL and a body read.
  • ALCOHOL GATE (verified in PA + LA): alcohol needs a logged-in, age-verified session — anonymous returns
    "alcohol products aren't available". NON-alcohol browses anonymously, so the free anon path is the
    proof-of-pipe + the whole non-alc long tail; bev-alc later needs an account session injected here.

Driver notes (free Playwright):
  • Headful by default under Xvfb (BROWSER_HEADFUL unset → headless=False) — matches the probe; the toughest
    fingerprinting sometimes needs a real window, and a datacenter Xvfb window cleared Instacart in the probe.
    Set BROWSER_HEADFUL=0 to force headless.
  • Channel `chrome` (real Google Chrome) first, bundled Chromium as fallback — Chrome's build id trusts more.
  • Network capture: `page.on("request")` records every `/graphql` URL into `self._gql`; open_zone reads the
    seed `SearchResultsPlacements` URL out of it (the ids only exist once a real search fires).
  • NO proxy is ever configured here. This connector is `cost_class: anti-bot` but runs $0 from a residential
    OR datacenter browser; per the standing rule it must NEVER acquire a per-GB proxy "to make it work".
"""
import json
import os
import re
import time
import urllib.parse

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aggregator import AggregatorConnector

GRAPHQL = "https://www.instacart.com/graphql"
SEARCH_OP = "SearchResultsPlacements"
SEARCH_HASH = "6f8d4a3f450d8d25dbb87b6b5bcb82180a1b3c972366fb1fb7de816c05523f4a"
# non-membership groceries to prefer (membership warehouses block the product query)
GROCERY = ["ALDI", "Target", "Publix", "Kroger", "Rouses", "Wegmans", "GIANT", "Food Lion",
           "Meijer", "Safeway", "Albertsons", "The Fresh Market", "Sprouts"]
BLOCK_HINTS = ["press & hold", "press and hold", "are you a robot", "unusual traffic", "access denied",
               "enter the characters", "verify you are human"]
UA = os.environ.get("BROWSER_UA",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36")
# Minimal stealth — undo the vanilla-automation tells (same patches browser_warm.py uses).
_STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""


class Instacart(AggregatorConnector):
    source = "instacart"

    def __init__(self, session="ic"):
        self.session = session
        self._pw = self._browser = self._ctx = self._page = None
        self._gql = []                                   # every /graphql request URL the page fires

    # ---- free browser lifecycle (no BD, no proxy) ----
    def _launch(self):
        from playwright.sync_api import sync_playwright
        headful = os.environ.get("BROWSER_HEADFUL", "1") != "0"
        self._pw = sync_playwright().start()
        last = None
        for ch in ("chrome", None):                      # real Chrome first, bundled Chromium fallback
            try:
                self._browser = (self._pw.chromium.launch(headless=not headful, channel=ch,
                                                           args=["--no-sandbox"]) if ch
                                 else self._pw.chromium.launch(headless=not headful, args=["--no-sandbox"]))
                break
            except Exception as e:
                last = e
        if not self._browser:
            raise RuntimeError("could not launch a free browser (channel chrome/bundled): %s" % last)
        self._ctx = self._browser.new_context(locale="en-US", user_agent=UA)
        self._ctx.add_init_script(_STEALTH)
        self._page = self._ctx.new_page()
        self._page.on("request", lambda r: self._gql.append(r.url) if "/graphql" in r.url else None)

    def close(self):
        for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
            try:
                getattr(obj, meth)() if obj else None
            except Exception:
                pass
        self._browser = self._pw = self._ctx = self._page = None

    def open_session(self, address=None):
        if not self._page:
            self._launch()
        self._page.goto("https://www.instacart.com/", wait_until="domcontentloaded", timeout=60000)
        self._page.wait_for_timeout(5000)
        body = (self._page.content() or "").lower()
        if any(h in body for h in BLOCK_HINTS):
            raise RuntimeError("Instacart anti-bot blocked the free browser (datacenter IP?) — "
                               "run this source on the residential executor, NOT a paid proxy")
        self._set_zip(address)
        return self.session

    def _set_zip(self, address):
        """Best-effort: set a delivery ZIP so a real storefront/zone resolves. `address` may be a bare ZIP."""
        zip_ = ""
        if address:
            m = re.search(r"\b(\d{5})\b", str(address))
            zip_ = m.group(1) if m else ""
        if not zip_:
            return
        for sel in ('input[data-testid="address-input"]', 'input[placeholder*="ZIP"]',
                    'input[placeholder*="address" i]', 'input[type="text"]'):
            try:
                el = self._page.query_selector(sel)
                if el and el.is_visible():
                    el.click(); el.fill(zip_); self._page.wait_for_timeout(1500)
                    self._page.keyboard.press("Enter"); self._page.wait_for_timeout(4000)
                    return
            except Exception:
                pass

    def open_zone(self, session, address=None):
        """Enter a regular (non-membership) grocery so shop context is set, run one seed search to fire a
        live SearchResultsPlacements request, and read {slug, shopId, postalCode, zoneId} back out of it."""
        slug = ""
        for name in GROCERY:
            try:
                link = self._page.get_by_text(re.compile(re.escape(name), re.I)).first
                if link and link.is_visible():
                    link.click(timeout=5000); self._page.wait_for_timeout(6000)
                    break
            except Exception:
                continue
        m = re.search(r"/store/([^/?\"]+)", self._page.url)
        slug = m.group(1) if m else ""
        # seed search — fires the SearchResultsPlacements request whose variables carry the zone ids
        self._gql = []
        try:
            box = self._page.query_selector('input[type="search"], input[placeholder*="Search" i]')
            if box:
                box.click(); box.fill("milk"); self._page.keyboard.press("Enter")
                self._page.wait_for_timeout(6000)
        except Exception:
            pass
        url = next((u for u in self._gql if SEARCH_OP in u), "")
        if not url:                                      # fall back to navigating a raw seed search URL
            if slug:
                self._page.goto("https://www.instacart.com/store/%s/s?k=milk" % slug,
                                wait_until="domcontentloaded", timeout=60000)
                self._page.wait_for_timeout(6000)
                url = next((u for u in self._gql if SEARCH_OP in u), "")
        if not url:
            raise RuntimeError("no %s call captured for slug %r (membership wall / not entered?)"
                               % (SEARCH_OP, slug))
        v = json.loads(urllib.parse.unquote(
            urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["variables"][0]))
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
        for _ in range(3):                               # raw-graphql nav is occasionally flaky — retry
            try:
                self._page.goto(self._search_url(zone, query), wait_until="domcontentloaded", timeout=60000)
                self._page.wait_for_timeout(2500)
                txt = self._page.inner_text("body")
            except Exception:
                time.sleep(2); continue
            m = re.search(r"(\{.*\})", txt, re.S)
            if m:
                try:
                    d = json.loads(m.group(1))
                    placements = (((d.get("data") or {}).get("searchResultsPlacements") or {})
                                  .get("placements")) or []
                    return placements, None              # SearchResultsPlacements isn't cursor-paged in this op
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
        price_str = json.dumps(it)                       # price lives in a nested viewSection; pull first $x.xx
        pm = re.search(r"\$(\d+\.\d{2})", price_str)
        img = re.search(r'https://[^"\\]+?\.(?:png|jpe?g)[^"\\]*', price_str)
        return dict(retailer=zone.get("slug", retailer), store=zone.get("slug", retailer),
                    store_id=zone.get("shopId"), zone=zone.get("zoneId"),
                    product_id=str(it.get("id") or it.get("legacyId") or ""), upc="",
                    brand="", name=it.get("name", ""), category="", size=it.get("size", ""),
                    price=float(pm.group(1)) if pm else None, in_stock=True,
                    image_url=img.group(0) if img else "", url="",
                    raw_json=json.dumps(it)[:4000])

    # ensure the browser is always torn down, even though the base pull() doesn't know about it
    def pull(self, *a, **k):
        try:
            return super().pull(*a, **k)
        finally:
            self.close()
