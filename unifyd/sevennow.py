#!/usr/bin/env python3
"""sevennow.py — 7-Eleven store-level catalog + EXACT per-store inventory via 7NOW (7now.com).

7NOW is 7-Eleven's OWN delivery service (first-party, not a third-party aggregator), so its per-store
catalog is fair game. It exposes a clean JSON BFF that — critically — returns the **exact on-hand unit count
per product per store** (`store_quantity`), plus UPC, price, brand, size, and an `age_restricted` flag that
marks alcohol. The department list is store-driven: alcohol markets (e.g. Austin) expose **Beer & Seltzer**
and **Wine** departments; hemp/THC beverages surface the same way in stores that carry them. This turns
~9,000 7-Eleven stores into a per-store beer/wine (+ hemp) inventory source — a huge blow-out of the chain
list, and better data than in/out (it's a real count).

THE RECIPE (all under https://www.7now.com/api/bff/):
  1. store resolution  — an address → lat/lon → store_id (via the site's location-services; we seed known
                         (store_id, lat, lon) or resolve in a warmed browser). store_id is the key to everything.
  2. departments       — per store; global 7-Eleven category IDs (Beer&Seltzer=48392, Wine=48407, Drinks=48395,
                         …). We probe each known dept id against the store and keep the ones that return items.
  3. products          — GET unified-catalog/v1/conv/inventory/subcategories?storeId=<S>&categoryId=<C>&skip&limit
                         → products grouped by subcategory. Per product: name, brand, upc, price(¢),
                         `store_quantity` (EXACT on-hand), availableQuantity (orderable cap, ≤100), available,
                         age_restricted, size_value, slin, product_id, images, long_desc.
  4. store summary     — content/homepage?…&store_id=<S> .inventory = {active, outOfStock}.

INCAPSULA: 7now.com is behind Imperva/Incapsula bot protection. The gating token (`reese84`) is JS-readable
(NOT HttpOnly), so a browser session that has solved the challenge can be exported and replayed — the same
warmed-cookie pattern we use for TTB / Total Wine. Two fetch backends:
  • --cookie "<cookie header>"  : a warmed cookie string (reese84 + incap_ses + visid_incap) + matching UA,
                                  replayed via urllib. Runs from the SAME machine/IP that warmed it, within the
                                  token TTL (re-warm every ~hour). Best for local Mac runs.
  • (default, no cookie)        : prints the one-liner to warm+export a cookie from a browser. For unattended
                                  server runs, drive a warmed browser / BD Browser session and call the same
                                  endpoints in-page (see chains-off-bd / delivery-aggregators pattern).

Lands `sevennow_products` (full latest snapshot + raw + image) and the dated observe.record time-series
(qty=store_quantity). Hemp beverages are flagged (hemp_scan) so they route into the hemp inventory view.

    python sevennow.py --cookie "reese84=…; incap_ses_…=…; visid_incap_…=…" --store 36999 --lat 30.2729 --lon -97.7444
    python sevennow.py --cookie "$SEVENNOW_COOKIE"        # all seeded stores
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe
import hemp_scan

API = "https://www.7now.com/api/bff/unified-catalog/v1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

# Global 7-Eleven department category IDs (store-independent ids; presence is store-DEPENDENT — probe each).
# `alcohol`/`hemp` flags drive the bev-alc + hemp cut. Liquor/Spirits appears in spirits-delivery markets under
# its own id; hemp/THC beverages surface either as their own dept or inside Drinks where a store carries them.
DEPARTMENTS = [
    ("48392", "Beer & Seltzer", True, False),
    ("48407", "Wine", True, False),
    ("48409", "Liquor", True, False),          # spirits markets (probed; absent where 7-Eleven can't ship spirits)
    ("48395", "Drinks", False, False),         # low-dose THC bev sometimes lands here — scanned for hemp
    ("48423", "Fresh Food", False, False),
    ("48397", "Snacks", False, False),
    ("48398", "Candy", False, False),
    ("48396", "Ice Cream", False, False),
    ("48402", "Grocery", False, False),
    ("48401", "Household", False, False),
    ("48400", "Personal Care", False, False),
    ("48408", "Tobacco", False, False),
    ("78530", "Cigarettes", False, False),
]

# Known store seeds (store_id, city, lat, lon). Extend as we resolve more markets. Austin carries Beer+Wine;
# Chicago downtown (33727) is alcohol-free — a good pair to prove the store-driven department list.
STORE_SEEDS = [
    {"store_id": "36999", "city": "Austin TX", "lat": 30.272931800000002, "lon": -97.74442549999999},
    {"store_id": "33727", "city": "Chicago IL", "lat": 41.885315, "lon": -87.65479909999999},
]

PROD_FIELDS = ["store_id", "store_city", "department", "department_id", "category", "subcategory",
               "product_id", "slin", "upc", "name", "brand", "size", "price", "original_price",
               "available", "available_quantity", "store_quantity", "age_restricted", "is_hemp",
               "hemp_signal", "image", "long_desc", "captured_at", "source", "raw_json"]


class Fetcher:
    """urllib GET with a warmed Incapsula cookie. Raises Blocked when Incapsula serves its challenge HTML."""
    class Blocked(RuntimeError):
        pass

    def __init__(self, cookie, ua=UA):
        self.cookie, self.ua = cookie, ua

    def get(self, url, timeout=25):
        req = urllib.request.Request(url, headers={
            "User-Agent": self.ua, "Accept": "application/json",
            "Cookie": self.cookie, "Referer": "https://www.7now.com/",
            "x-requested-with": "XMLHttpRequest"})
        body = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
        if body[:15].lower().startswith("<!doctype html") or "Incapsula" in body[:400]:
            raise Fetcher.Blocked("Incapsula challenge — cookie stale/invalid; re-warm in a browser")
        return json.loads(body)


def _to_price(cents):
    try:
        return round(int(cents) / 100.0, 2)
    except Exception:
        return None


def department_products(fetch, store, cat_id, log=print):
    """All products in a store department (paged over skip). Products come grouped by subcategory."""
    out, skip, limit = [], 0, 100
    while True:
        url = "%s/conv/inventory/subcategories?storeId=%s&categoryId=%s&skip=%d&limit=%d" % (
            API, store["store_id"], cat_id, skip, limit)
        try:
            j = fetch.get(url)
        except Fetcher.Blocked:
            raise
        except Exception as e:
            log("    cat %s skip %d: %s" % (cat_id, skip, str(e)[:50])); break
        subs = j.get("subcategory") or []
        page = []
        for s in subs:
            sub_name = s.get("subcategory") or s.get("name") or ""
            for p in (s.get("items") or []):
                page.append((sub_name, p))
        out.extend(page)
        # subcategories bundles the whole department in one response; stop unless a full page suggests more
        if len(page) < limit:
            break
        skip += limit
        time.sleep(0.4)
    return out


def store_departments(fetch, store, log=print):
    """Probe each known department against the store; keep those that return products."""
    live = []
    for cat_id, name, alcohol, hemp in DEPARTMENTS:
        try:
            prods = department_products(fetch, store, cat_id, log=log)
        except Fetcher.Blocked:
            raise
        if prods:
            live.append((cat_id, name, alcohol, hemp, prods))
            log("    %-16s %4d items%s" % (name, len(prods), "  [ALCOHOL]" if alcohol else ""))
        time.sleep(0.3)
    return live


def harvest_store(fetch, store, log=print):
    """Full per-store catalog with exact counts. Returns (snapshot_records, observation_rows)."""
    log("  store %s (%s)" % (store["store_id"], store.get("city", "")))
    ts = int(time.time())
    live = store_departments(fetch, store, log=log)
    snap, obs = [], []
    for cat_id, dept, alcohol, hemp_dept, prods in live:
        for sub_name, p in prods:
            name = p.get("name") or ""
            cat = p.get("category") or dept
            sig = hemp_scan.classify(name, cat) or (hemp_scan.classify(name, sub_name) if sub_name else None)
            is_hemp = bool(sig) or hemp_dept
            sq = p.get("store_quantity")
            snap.append({
                "store_id": store["store_id"], "store_city": store.get("city", ""),
                "department": dept, "department_id": cat_id, "category": cat, "subcategory": sub_name,
                "product_id": str(p.get("product_id") or ""), "slin": str(p.get("slin") or ""),
                "upc": str(p.get("upc") or ""), "name": name, "brand": p.get("brand") or "",
                "size": p.get("size_value") or "", "price": _to_price(p.get("price")),
                "original_price": _to_price(p.get("original_price")),
                "available": bool(p.get("available")), "available_quantity": p.get("availableQuantity"),
                "store_quantity": sq, "age_restricted": bool(p.get("age_restricted")),
                "is_hemp": is_hemp, "hemp_signal": sig or "",
                "image": (p.get("images") or [p.get("thumbnail")])[0] if (p.get("images") or p.get("thumbnail")) else "",
                "long_desc": (p.get("long_desc") or "")[:500], "captured_at": ts, "source": "sevennow",
                "raw_json": json.dumps(p, separators=(",", ":"))[:4000]})
            obs.append({
                "store": store.get("city", ""), "store_id": store["store_id"],
                "product_id": str(p.get("product_id") or ""), "upc": str(p.get("upc") or ""),
                "brand": p.get("brand") or "", "name": name, "price": _to_price(p.get("price")),
                "on_promo": bool(p.get("promos")), "in_stock": bool(p.get("available")),
                "qty": sq,                                   # EXACT on-hand — the whole point
                "stock_level": p.get("availabilityMessage") or "", "is_hemp": is_hemp})
    log("  -> %d products (%d alcohol, %d hemp)" % (
        len(snap), sum(1 for r in snap if r["age_restricted"]), sum(1 for r in snap if r["is_hemp"])))
    return snap, obs


def run(cookie, stores=None, land=True, log=print):
    if not cookie:
        log("[sevennow] no cookie. Warm one in a browser at https://www.7now.com (set a delivery address),")
        log("           then in DevTools console run:  copy(document.cookie)")
        log("           and pass it as --cookie \"<paste>\" (reese84 is JS-readable; TTL ~1h, re-warm as needed).")
        return []
    stores = stores or STORE_SEEDS
    fetch = Fetcher(cookie)
    all_snap = []
    for store in stores:
        try:
            snap, obs = harvest_store(fetch, store, log=log)
        except Fetcher.Blocked as e:
            log("  BLOCKED: %s" % e); break
        if land and snap:
            warehouse.write_accumulate("sevennow_products", snap,
                                       key=lambda r: (r["store_id"], r["product_id"]), fields=PROD_FIELDS)
            observe.record("sevennow", obs, part="%s_sevennow_%s" % (time.strftime("%Y-%m-%d"), store["store_id"]),
                           log=log)
        all_snap.extend(snap)
        time.sleep(1.0)
    hemp = sum(1 for r in all_snap if r["is_hemp"]); alc = sum(1 for r in all_snap if r["age_restricted"])
    log("[sevennow] DONE: %d products across %d stores -> sevennow_products (%d alcohol, %d hemp) + observations"
        % (len(all_snap), len({r["store_id"] for r in all_snap}), alc, hemp))
    return all_snap


def main(argv=None):
    ap = argparse.ArgumentParser(description="7-Eleven per-store catalog + exact inventory via 7NOW.")
    ap.add_argument("--cookie", default=os.environ.get("SEVENNOW_COOKIE", ""),
                    help="warmed 7now.com cookie header (or set SEVENNOW_COOKIE)")
    ap.add_argument("--store", help="single store_id (with --lat/--lon)")
    ap.add_argument("--city", default="")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--no-land", action="store_true")
    a = ap.parse_args(argv)
    stores = None
    if a.store:
        stores = [{"store_id": a.store, "city": a.city or a.store, "lat": a.lat, "lon": a.lon}]
    recs = run(a.cookie, stores=stores, land=not a.no_land)
    for r in recs[:25]:
        if r["age_restricted"] or r["is_hemp"]:
            print("  %-30s %-8s $%-6s qty=%-4s %s" % (
                (r["name"] or "")[:30], r["size"], r["price"], r["store_quantity"], r["department"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
