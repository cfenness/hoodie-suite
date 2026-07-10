"""aggregator.py — shared harness for delivery-marketplace connectors (Instacart, DoorDash, Uber Eats).

Build the hard parts ONCE; each platform supplies only its own API specifics. Pulling all three gets us
close to a complete retail catalog (grocery + convenience + liquor long tail), so the whole point is to
NOT rebuild the plumbing three times.

RECON (2026-07-10): all three are the SAME shape — location-keyed, Forter/reCAPTCHA-defended SPAs with an
internal GraphQL/REST API. Products + prices only load once a delivery ZONE is set (address or geolocation).
Local headless Playwright loads pages but the zone-set INTERACTION is blocked by Forter — so the browser
layer must be Bright Data's Browser API (anti-bot) or a hardened stealth browser. That layer is pluggable
here (`open_session`), so each platform subclass only writes its API calls, not the anti-bot fight.

    Shared (this module):  session/zone lifecycle · paged fetch loop · dedup · normalize to one row shape ·
                           land (→ <conn>_products snapshot w/ raw+image+hemp, → retail_observations series)
    Per-platform (subclass): open_zone(address) · fetch_page(query, cursor) · parse_item(raw)

Confirmed per-platform specifics to fill in:
    instacart  GraphQL www.instacart.com/graphql (op VisitShop seen) + v3 REST /v3/retailers/<id>/...
    doordash   internal /graphql (convenience/liquor "stores") — recon TBD
    ubereats   internal API (stores/catalog) — recon TBD, largely overlaps doordash
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

# common normalized row — every platform maps its raw item onto this before landing
ROW_FIELDS = ["source", "retailer", "store", "store_id", "zone", "product_id", "upc", "brand", "name",
              "category", "size", "price", "promo", "on_promo", "in_stock", "qty", "image_url",
              "is_hemp", "url", "raw_json"]


class AggregatorConnector:
    """Base class. A platform subclass sets `source` and implements open_zone / fetch_page / parse_item."""
    source = "aggregator"

    def open_session(self, address):
        """Return an object that can talk to the platform's API for the given delivery ADDRESS/zone.
        Default = Bright Data Browser API (defeats Forter/reCAPTCHA); a subclass may override with a
        direct API session once the zone token is obtained. Raises if the browser layer isn't configured."""
        raise NotImplementedError("wire the Bright Data Browser API session (BROWSER_AUTH) here")

    # ---- per-platform hooks ----
    def open_zone(self, session, address):
        """Set the delivery zone (address or geolocation) so the catalog/prices become visible.
        Return a zone context (ids/tokens/cookies) the fetch calls need."""
        raise NotImplementedError

    def fetch_page(self, session, zone, query, cursor=None):
        """Return (raw_items, next_cursor) for one page of a search/category query."""
        raise NotImplementedError

    def parse_item(self, raw, retailer, zone):
        """Map one raw API item onto ROW_FIELDS (dict). Set qty only if the platform gives a count."""
        raise NotImplementedError

    # ---- shared engine (works for every platform) ----
    def pull(self, address, retailers, queries, per_query_pages=3, log=print):
        """Drive the whole pull for one zone: for each retailer × query, page through, normalize, dedup,
        and land INCREMENTALLY (snapshot + dated observations) so progress is saved + a restart is safe."""
        session = self.open_session(address)
        zone = self.open_zone(session, address)
        seen, uniq = set(), []
        for retailer in retailers:
            for q in queries:
                cursor, pages = None, 0
                while pages < per_query_pages:
                    raw_items, cursor = self.fetch_page(session, zone, q, cursor)
                    for raw in (raw_items or []):
                        row = self.parse_item(raw, retailer, zone)
                        if not row:
                            continue
                        row.setdefault("source", self.source)
                        row["is_hemp"] = observe.is_hemp(row.get("brand"), row.get("name"), row.get("category"))
                        k = (row.get("store_id"), row.get("product_id"))
                        if k in seen:
                            continue
                        seen.add(k); uniq.append(row)
                    pages += 1
                    if not cursor:
                        break
            self._land(uniq, log)                # incremental land after each retailer
            log("  [%s] %s — %d products so far" % (self.source, retailer, len(uniq)))
        self._land(uniq, log)
        log("[%s] DONE %d products across %d stores" % (self.source, len(uniq),
                                                        len({r.get("store_id") for r in uniq})))
        return uniq

    def _land(self, uniq, log=print):
        """Snapshot (full raw + image) → <source>_products; lean dated rows → retail_observations."""
        warehouse.write_parquet("%s_products" % self.source, [{k: r.get(k) for k in ROW_FIELDS} for r in uniq])
        observe.record(self.source, [dict(store=r.get("store"), store_id=r.get("store_id"),
                                           product_id=r.get("product_id"), upc=r.get("upc"),
                                           brand=r.get("brand"), name=r.get("name"), price=r.get("price"),
                                           promo=r.get("promo"), on_promo=r.get("on_promo"),
                                           in_stock=r.get("in_stock"), qty=r.get("qty"),
                                           is_hemp=r.get("is_hemp")) for r in uniq], log=log)
