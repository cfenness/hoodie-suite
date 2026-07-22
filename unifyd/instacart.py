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
import observe


def _find_items(o, out):
    """Recursively collect every Instacart item node (`Item*` typename with a name/id) from a placement tree."""
    if isinstance(o, dict):
        if str(o.get("__typename", "")).startswith("Item") and (o.get("name") or o.get("id")):
            out.append(o)
        for v in o.values():
            _find_items(v, out)
    elif isinstance(o, list):
        for v in o:
            _find_items(v, out)

GRAPHQL = "https://www.instacart.com/graphql"
SEARCH_OP = "SearchResultsPlacements"
SEARCH_HASH = "6f8d4a3f450d8d25dbb87b6b5bcb82180a1b3c972366fb1fb7de816c05523f4a"
# non-membership groceries to prefer (membership warehouses block the product query)
GROCERY = ["ALDI", "Target", "Publix", "Kroger", "Rouses", "Wegmans", "GIANT", "Food Lion",
           "Meijer", "Safeway", "Albertsons", "The Fresh Market", "Sprouts"]
# A broad, department-spanning basket — a per-store INVENTORY probe (what's in stock + at what stock level),
# meant to take as wide an in-stock cross-section per store as search allows. Each term returns up to `first`
# items; more TERMS (breadth across departments) matters more than depth per term.
INVENTORY_QUERIES = [
    # dairy & eggs
    "milk", "eggs", "cheese", "butter", "yogurt", "cream", "sour cream",
    # produce
    "bananas", "apples", "lettuce", "tomatoes", "onions", "potatoes", "avocado", "berries", "grapes",
    # meat & seafood
    "chicken breast", "ground beef", "bacon", "sausage", "pork", "salmon", "shrimp", "deli turkey",
    # bakery & bread
    "bread", "bagels", "tortillas", "buns",
    # pantry / center store
    "cereal", "rice", "pasta", "pasta sauce", "peanut butter", "canned soup", "beans", "flour", "sugar",
    "cooking oil", "coffee", "tea", "snack bars",
    # beverages
    "water", "soda", "juice", "sports drink", "sparkling water", "energy drink",
    # alcohol (returns availability where the store surfaces it)
    "beer", "wine", "vodka", "whiskey", "tequila", "hard seltzer",
    # frozen
    "ice cream", "frozen pizza", "frozen vegetables",
    # snacks & candy
    "chips", "cookies", "crackers", "candy", "popcorn",
    # household & baby & pet
    "paper towels", "toilet paper", "laundry detergent", "dish soap", "trash bags", "diapers", "dog food",
]
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

    def __init__(self, session="ic", target_retailer=None):
        self.session = session
        self.target_retailer = target_retailer           # e.g. "Publix" — enter THIS storefront, not the first grocery
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
        """Set a delivery ZIP so a REGIONAL chain (Publix) resolves. Instacart commits an address via an
        autocomplete dropdown, not a bare Enter — so type the zip, wait for suggestions, and CLICK the first
        one. Returns True if an address input was found + a suggestion clicked."""
        zip_ = ""
        if address:
            m = re.search(r"\b(\d{5})\b", str(address))
            zip_ = m.group(1) if m else ""
        if not zip_:
            return False
        # 1) reveal the address input (a header location button opens it on some layouts)
        for opener in ('button[aria-label*="address" i]', 'button[aria-label*="location" i]',
                       '[data-testid*="address" i] button', 'button:has-text("your address")'):
            try:
                b = self._page.query_selector(opener)
                if b and b.is_visible():
                    b.click(); self._page.wait_for_timeout(1200); break
            except Exception:
                pass
        # 2) type the zip into the address field, 3) click the first autocomplete suggestion
        for sel in ('input[data-testid="address-input"]', 'input[id*="address" i]',
                    'input[placeholder*="ZIP" i]', 'input[placeholder*="address" i]',
                    'input[placeholder*="delivery" i]', 'input[autocomplete="address-line1"]'):
            try:
                el = self._page.query_selector(sel)
                if not (el and el.is_visible()):
                    continue
                el.click(); el.fill(""); el.type(zip_, delay=60)
                self._page.wait_for_timeout(2500)
                for opt in ('[role="option"]', 'ul[role="listbox"] li', '[data-testid*="suggestion" i]',
                            'li[id*="option" i]'):
                    try:
                        first = self._page.query_selector(opt)
                        if first and first.is_visible():
                            first.click(); self._page.wait_for_timeout(4000)
                            return True
                    except Exception:
                        pass
                # no dropdown — fall back to Enter (older layout)
                self._page.keyboard.press("Enter"); self._page.wait_for_timeout(4000)
                return True
            except Exception:
                pass
        return False

    def _visible_retailers(self, limit=20):
        """The retailer names Instacart is offering for the current zone — so a miss tells us whether the
        chain wasn't offered (address didn't take) vs. our click missed it."""
        names = []
        try:
            for el in self._page.query_selector_all('a[href*="/store/"], img[alt]'):
                t = (el.get_attribute("alt") or el.inner_text() or "").strip()
                t = re.sub(r"\s+", " ", t)[:40]
                if t and t not in names:
                    names.append(t)
                if len(names) >= limit:
                    break
        except Exception:
            pass
        return names

    def open_zone(self, session, address=None):
        """Enter a regular (non-membership) grocery so shop context is set, run one seed search to fire a
        live SearchResultsPlacements request, and read {slug, shopId, postalCode, zoneId} back out of it."""
        slug = ""
        # If a specific retailer is targeted (e.g. Publix), enter THAT storefront; else fall back to the
        # first non-membership grocery on offer. Targeting first means a zip with no Publix simply yields no
        # Publix zone (we skip it) rather than silently pulling ALDI.
        order = ([self.target_retailer] if self.target_retailer else []) + \
                [g for g in GROCERY if g.lower() != (self.target_retailer or "").lower()]
        for name in order:
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

    def _search_url(self, zone, query, first=60):        # take as many per query as the op will return
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
                    items = []                           # flatten to EVERY item node (a grid is ~24/query, not 1)
                    _find_items(placements, items)
                    return items, None                   # SearchResultsPlacements isn't cursor-paged in this op
                except Exception:
                    pass
            time.sleep(2)
        return [], None

    def parse_item(self, it, retailer, zone):
        """Map ONE Instacart `ItemsItem` node onto a row, reading the REAL per-store inventory signal.

        The item carries `availability{available, stockLevel}` (e.g. "highlyInStock"/"outOfStock") — that,
        keyed by store_id, IS the inventory (lands in retail_observations.stock_level/in_stock). Price is the
        structured `price.viewSection.priceValueString` (regex `$x.xx` only as a fallback), brand is
        `brandName`, size is `size`."""
        if not isinstance(it, dict) or not (it.get("name") or it.get("id")):
            return None
        # availability — the real stock signal (default to in-stock only when the node omits availability)
        av = it.get("availability") or {}
        avs = av.get("viewSection") or {}
        available = av.get("available")
        in_stock = bool(available) if available is not None else True
        stock_level = av.get("stockLevel") or avs.get("stockLevelLabelString") or ""
        # price — structured value first, `$x.xx` regex only as a fallback
        price = None
        pv = (it.get("price") or {}).get("viewSection") or {}
        raw_price = pv.get("priceValueString") or pv.get("priceString")
        if raw_price:
            m = re.search(r"(\d+(?:\.\d+)?)", str(raw_price).replace(",", ""))
            price = float(m.group(1)) if m else None
        if price is None:
            pm = re.search(r"\$(\d+\.\d{2})", json.dumps(it))
            price = float(pm.group(1)) if pm else None
        # product image lives under the item's own viewSection (search only there so we don't grab the
        # stock-status icon asset that sits under availability.viewSection)
        img = re.search(r'https://[^"\\]+?\.(?:png|jpe?g)[^"\\]*', json.dumps(it.get("viewSection") or {}))
        return dict(retailer=zone.get("slug", retailer), store=zone.get("slug", retailer),
                    store_id=zone.get("shopId"), zone=zone.get("zoneId"),
                    product_id=str(it.get("id") or it.get("legacyId") or it.get("productId") or ""), upc="",
                    brand=it.get("brandName") or "", name=it.get("name", ""), category="",
                    size=it.get("size", "") or "", price=price, in_stock=in_stock, stock_level=stock_level,
                    image_url=img.group(0) if img else "", url="", raw_json=json.dumps(it)[:4000])

    def sweep(self, zips, retailer="Publix", queries=None, per_query_pages=1, log=print):
        """Sweep ONE retailer (e.g. Publix) across a list of delivery zips — each zip resolves to the nearest
        store, so a footprint of zips ≈ that chain's stores. For each zip: set the zip, ENTER that retailer's
        storefront (skip the zip if it isn't offered there — never fall back to another chain), and probe the
        INVENTORY_QUERIES basket. Lands incrementally (accumulate) so a long sweep is restart-safe and dedups
        stores that two zips share. Returns the accumulated rows."""
        queries = queries or INVENTORY_QUERIES
        self.target_retailer = retailer
        if not self._page:
            self._launch()
        seen, uniq, stores, skipped = set(), [], {}, 0
        try:
            for i, z in enumerate(zips):
                try:
                    self.open_session(address=z)
                except Exception as e:
                    skipped += 1; log("  [instacart] zip %s: session blocked (%s)" % (z, str(e)[:70])); continue
                offered = self._visible_retailers()     # what this zone actually offers (diagnostic)
                try:
                    zone = self.open_zone(self.session, address=z)
                except Exception as e:
                    skipped += 1
                    log("  [instacart] zip %s: no zone (%s) | offered: %s"
                        % (z, str(e)[:60], ", ".join(offered[:12]) or "?")); continue
                slug = (zone.get("slug") or "")
                if retailer.lower().replace(" ", "") not in slug.lower().replace("-", ""):
                    skipped += 1
                    log("  [instacart] zip %s entered %r ≠ %s (resolved postalCode=%s) | offered: %s"
                        % (z, slug, retailer, zone.get("postalCode"), ", ".join(offered[:12]) or "?"))
                    continue
                if zone.get("shopId") in stores:         # this zip maps to a store we already swept
                    log("  [instacart] zip %s -> %s (dup store, skip)" % (z, slug)); continue
                n0 = len(uniq)
                for q in queries:
                    raw, _ = self.fetch_page(self.session, zone, q, None)
                    for it in (raw or []):
                        row = self.parse_item(it, retailer, zone)
                        if not row:
                            continue
                        row.setdefault("source", self.source)
                        row["is_hemp"] = observe.is_hemp(row.get("brand"), row.get("name"), row.get("category"))
                        k = (row.get("store_id"), row.get("product_id"))
                        if k in seen:
                            continue
                        seen.add(k); uniq.append(row)
                stores[zone.get("shopId")] = slug
                self._land(uniq, log)                     # incremental, keyed-merge (never shrinks)
                log("  [instacart] zip %s -> %s: +%d in-stock (%d stores, %d rows) [%d/%d]"
                    % (z, slug, len(uniq) - n0, len(stores), len(uniq), i + 1, len(zips)))
        finally:
            self.close()
        log("[instacart] SWEEP DONE %s: %d in-stock rows across %d stores (%d zips skipped)"
            % (retailer, len(uniq), len(stores), skipped))
        return uniq

    def pull_zones(self, zones, queries=None, log=print):
        """Replay SearchResultsPlacements DIRECTLY for explicit zones — each a dict
        {shopId, postalCode, zoneId, slug?}. No homepage / address / geolocation: the zone IS the location,
        so this reaches a REGIONAL chain (Publix) that a datacenter IP would never surface on the homepage.
        Only a warmed page context (cookies/TLS) is needed, which one homepage load provides. Lands per-store
        inventory (in_stock + stock_level) incrementally. This is the robust path for chain-targeted pulls."""
        queries = queries or INVENTORY_QUERIES
        if not self._page:
            self._launch()
        try:                                             # one homepage load to warm cookies for the graphql replay
            self._page.goto("https://www.instacart.com/", wait_until="domcontentloaded", timeout=60000)
            self._page.wait_for_timeout(3000)
        except Exception:
            pass
        seen, uniq, stores = set(), [], {}
        try:
            for i, zone in enumerate(zones):
                if not (zone.get("shopId") and zone.get("postalCode") and zone.get("zoneId")):
                    log("  [instacart] zone %r missing shopId/postalCode/zoneId — skip" % zone); continue
                slug = zone.get("slug") or ("shop-%s" % zone.get("shopId"))
                n0 = len(uniq)
                for q in queries:
                    raw, _ = self.fetch_page(self.session, zone, q, None)
                    for it in (raw or []):
                        row = self.parse_item(it, slug, zone)
                        if not row:
                            continue
                        row.setdefault("source", self.source)
                        row["is_hemp"] = observe.is_hemp(row.get("brand"), row.get("name"), row.get("category"))
                        k = (row.get("store_id"), row.get("product_id"))
                        if k in seen:
                            continue
                        seen.add(k); uniq.append(row)
                stores[zone.get("shopId")] = slug
                self._land(uniq, log)
                log("  [instacart] zone shopId=%s (%s): +%d in-stock (%d rows, %d stores) [%d/%d]"
                    % (zone.get("shopId"), slug, len(uniq) - n0, len(uniq), len(stores), i + 1, len(zones)))
        finally:
            self.close()
        log("[instacart] ZONES DONE: %d in-stock rows across %d stores" % (len(uniq), len(stores)))
        return uniq

    # ensure the browser is always torn down, even though the base pull() doesn't know about it
    def pull(self, *a, **k):
        try:
            return super().pull(*a, **k)
        finally:
            self.close()
