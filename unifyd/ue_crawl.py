#!/usr/bin/env python3
"""ue_crawl.py — NATIONAL UberEats crawler (bev-alc, on + off premise). Full capture from getStoreV1 (catalog) +
getMenuItemV1 (UPC / price / promo / recipe-customizations), across the whole US.

Architecture (every step proven — see the ue_probe*.py investigation):
  1. GEOCODE headless (mapsSearchV1 + getDeliveryLocationV1 via curl_cffi + residential proxy): any US
     city/ZIP/address -> an Uber location (reference + coords). No auth, no browser.
  2. Build a pl= feed URL from that location.
  3. ONE warmed real-Chrome session (through the residential proxy) navigates each zone's pl= URL -> the session
     is now located there -> getFeedV1 returns that zone's merchants.
  4. Per store: getStoreV1 (catalog) + getMenuItemV1 (full per-item detail incl. UPC + on-prem recipe). These
     replay in-session via the browser's own fetch — the reCAPTCHA/PX context is inherited.
  5. Land FULL capture to <site>_products (dedup by store_uuid|item_uuid) — resumable across overlapping zones.

Catalogs are ZONE-BOUND (a store's getStoreV1 only returns data when the session is in its area), so coverage =
enumerating US zones. Stores are deduped by uuid so overlapping zones don't re-crawl.

  python ue_crawl.py --zones-file zones_us.txt --max-stores 300         # all listed zones
  python ue_crawl.py --zones "Chicago, IL;Miami, FL" --max-stores 50    # ad-hoc
  python ue_crawl.py --site postmates ...
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resi
import browser_warm
import ubereats as ue
import warehouse

API = "https://www.ubereats.com/_p/api/"


# ── geocode (headless) ────────────────────────────────────────────────────────────────────────────────────────
def geocode(query, session=None):
    """query (city/ZIP/address) -> Uber location dict {address, reference, referenceType, latitude, longitude, …}
    via mapsSearchV1 + getDeliveryLocationV1. Headless curl_cffi through the residential proxy. None if no hit."""
    from curl_cffi import requests as cr
    px = resi._session_url(session or "uegc")
    s = cr.Session(impersonate="chrome", proxies={"http": px, "https": px} if px else None, timeout=35)
    H = {"content-type": "application/json", "accept": "application/json", "x-csrf-token": "x"}
    try:
        s.get("https://www.ubereats.com/")
        hits = (s.post(API + "mapsSearchV1", json={"query": query}, headers=H).json() or {}).get("data") or []
        if not hits:
            return None
        # avoid narrow POIs with ~no delivery coverage (airports, venues, freight); prefer a real place/locality
        BAD = {"AIRPORT", "TRAVEL_AND_TRANSPORTATION", "CARGO_TRANSPORTATION", "FREIGHT_FACILITY",
               "MUSIC_VENUE", "PERFORMING_ARTS", "STADIUM", "HOSPITAL"}
        pick = next((h for h in hits if not (set(h.get("categories") or []) & BAD)), hits[0])
        det = (s.post(API + "getDeliveryLocationV1", json={"placeId": pick["id"], "provider": "uber_places"},
                      headers=H).json() or {}).get("data")
        return det if det and det.get("latitude") is not None else None
    except Exception:
        return None


def pl_url(det, base="https://www.ubereats.com"):
    loc = {"address": det["address"]["title"], "reference": det["reference"],
           "referenceType": det["referenceType"], "latitude": det["latitude"], "longitude": det["longitude"]}
    b64 = base64.b64encode(urllib.parse.quote(json.dumps(loc, separators=(",", ":"))).encode()).decode()
    return "%s/feed?diningMode=DELIVERY&pl=%s" % (base, urllib.parse.quote(b64))


# ── resume ────────────────────────────────────────────────────────────────────────────────────────────────────
def landed_store_uuids(site="ubereats", table="products"):
    """Distinct store_uuids already landed, for resume/dedup. `table` picks the grain: coverage dedupes against
    `<site>_stores` (the store outlets it lands), deep dedupes against `<site>_products` (stores it item-crawled)."""
    try:
        rows = warehouse.query("%s_%s" % (site, table), "SELECT DISTINCT store_uuid FROM t WHERE store_uuid <> ''")
        return {r["store_uuid"] for r in rows}
    except Exception:
        return set()


# 2-letter state -> IPRoyal geo name (full, lowercase, dashed) for matching the proxy exit IP to the zone
US_STATES = {"AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
             "CO": "colorado", "CT": "connecticut", "DE": "delaware", "DC": "district-of-columbia",
             "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana",
             "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
             "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri",
             "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new-hampshire", "NJ": "new-jersey",
             "NM": "new-mexico", "NY": "new-york", "NC": "north-carolina", "ND": "north-dakota", "OH": "ohio",
             "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania", "RI": "rhode-island", "SC": "south-carolina",
             "SD": "south-dakota", "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
             "VA": "virginia", "WA": "washington", "WV": "west-virginia", "WI": "wisconsin", "WY": "wyoming"}


def zones_by_state(zones):
    """Group 'City, ST' zone queries by US state (full lowercase name for proxy geo-targeting). Unparseable
    zones fall under 'us' (no geo pin)."""
    import re as _re
    groups = {}
    for z in zones:
        m = _re.search(r",\s*([A-Z]{2})\s*$", z.strip())
        st = US_STATES.get(m.group(1)) if m else None
        groups.setdefault(st or "us", []).append(z)
    return groups


# ── store OUTLET capture (geo) — feeds src_outlets + the coverage map ─────────────────────────────────────────
STORE_FIELDS = ["store_uuid", "store_name", "lat", "lng", "address", "city", "state", "postal_code",
                "phone", "chain", "source", "url", "captured_at", "raw_json"]


def _store_outlet(payloads, uuid, name, site):
    """Dig the store's own geo + address out of the getStoreV1 payload → an outlet row for src_outlets / the map.
    Walks generically (schema varies) for latitude/longitude + the address parts. None if no coords found."""
    got = {"lat": None, "lng": None, "address": "", "city": "", "state": "", "postal_code": "", "phone": ""}

    def walk(o):
        if isinstance(o, dict):
            if got["lat"] is None and isinstance(o.get("latitude"), (int, float)) and isinstance(o.get("longitude"), (int, float)):
                got["lat"], got["lng"] = o["latitude"], o["longitude"]
            for k, dst in (("streetAddress", "address"), ("address1", "address"), ("title", "address"),
                           ("city", "city"), ("region", "state"), ("state", "state"),
                           ("postalCode", "postal_code"), ("phoneNumber", "phone"), ("phone", "phone")):
                v = o.get(k)
                if v and isinstance(v, str) and not got[dst]:
                    got[dst] = v
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for pl in payloads:
        walk(pl)
    if got["lat"] is None:
        return None
    import time as _t
    return {"store_uuid": uuid, "store_name": name, "lat": got["lat"], "lng": got["lng"],
            "address": got["address"], "city": got["city"], "state": (got["state"] or "")[:2].upper() if len(got["state"] or "") == 2 else got["state"],
            "postal_code": got["postal_code"], "phone": got["phone"], "chain": "", "source": site,
            "url": "%s/store/x/%s" % (ue.SITES.get(site, ue.SITES["ubereats"])["base"], uuid),
            "captured_at": int(_t.time()), "raw_json": ""}


# ── FAST coverage: extract every merchant's geo from the feed's mapMarkers (no per-store nav) ──────────────────
def _feed_outlets(feed_payloads, site):
    """Walk getFeedV1 payload(s) → a geo'd outlet per merchant. Each store card carries storeUuid + title +
    actionUrl and a parallel mapMarker{latitude,longitude,description.title}. 300+ stores per feed, in seconds."""
    import time as _t
    base = ue.SITES.get(site, ue.SITES["ubereats"])["base"]
    out = {}

    def walk(o):
        if isinstance(o, dict):
            mm = o.get("mapMarker")
            su = o.get("storeUuid")
            if su and isinstance(mm, dict) and isinstance(mm.get("latitude"), (int, float)):
                nm = ((mm.get("description") or {}).get("title")) or ""
                if not nm:
                    t = o.get("title")
                    nm = t if isinstance(t, str) else (t or {}).get("text", "") if isinstance(t, dict) else ""
                au = o.get("actionUrl") or ""
                out[su] = {"store_uuid": su, "store_name": nm, "lat": mm["latitude"], "lng": mm["longitude"],
                           "address": au, "city": "", "state": "", "postal_code": "", "phone": "", "chain": "",
                           "source": site, "url": base + au, "captured_at": int(_t.time()), "raw_json": ""}
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for pl in feed_payloads:
        walk(pl)
    return list(out.values())


def crawl_coverage(zones, site="ubereats", resume=True, geo_state=None, log=print):
    """FAST national coverage: per zone, load the feed once and harvest EVERY merchant's geo (mapMarker) +
    identity from getFeedV1 → land <site>_stores. No per-store navigation → hundreds of geo'd outlets per zone
    in seconds. `geo_state` (full US state name, e.g. 'illinois') routes the browser through a proxy IP in THAT
    state — UberEats' feed returns empty when the exit IP's geo conflicts with the zone, so a state-worker crawls
    its own state's zones through a state-matched IP (dodges home-IP flagging + runs parallel per state)."""
    cfg = ue.SITES.get(site, ue.SITES["ubereats"])
    ue._CUR.update(base=cfg["base"], domain=cfg["domain"], source=site)
    if geo_state and resi.enabled():
        os.environ["BROWSER_PROXY"] = resi.geo_session_url("cov" + geo_state.replace(" ", ""), state=geo_state) or ""
        log("[ue-cov] routing through a %s proxy IP" % geo_state)
    elif os.environ.get("UE_PROXY") == "1" and resi.enabled():
        os.environ["BROWSER_PROXY"] = resi._session_url("uecov") or ""
    else:
        os.environ.pop("BROWSER_PROXY", None)
    done = landed_store_uuids(site, "stores") if resume else set()   # coverage dedupes against the STORES it lands
    log("[ue-cov] site=%s | %d zones | %d stores already landed" % (site, len(zones), len(done)))
    tot = 0
    with browser_warm.Warmer(cfg["domain"], channel="chrome", headful=True) as w:
        ctx = w._ctx; p = w._page()
        feeds = []
        ctx.on("response", lambda r: _grab_feed(r, feeds))
        for zi, z in enumerate(zones):
            feeds.clear()
            url, label = (z, z) if z.startswith("http") else (None, z)
            if url is None:
                det = geocode(z, session="z%d" % zi)
                if not det:
                    log("[ue-cov] zone %d/%d '%s': geocode FAILED" % (zi + 1, len(zones), z)); continue
                url = pl_url(det, base=cfg["base"]); label = det["address"]["title"]
            try:
                p.goto(url, wait_until="domcontentloaded", timeout=60000)
                p.wait_for_timeout(4000); ue._clear_challenge(p, log)
                for _ in range(12):                      # wait for the first feed response
                    if feeds:
                        break
                    ue._clear_challenge(p, log); p.wait_for_timeout(1500)
                for _ in range(10):                      # scroll to load more merchants → more markers
                    p.mouse.wheel(0, 9000); p.wait_for_timeout(900)
                outlets = _feed_outlets(feeds, site)
                fresh = [o for o in outlets if o["store_uuid"] not in done]
                for o in fresh:
                    done.add(o["store_uuid"])
                if fresh:
                    warehouse.write_accumulate("%s_stores" % site, fresh, key=lambda r: r["store_uuid"], fields=STORE_FIELDS)
                    tot += len(fresh)
                log("[ue-cov] zone %d/%d %-22s %d markers, %d new (run total %d)" % (
                    zi + 1, len(zones), label[:22], len(outlets), len(fresh), tot))
            except Exception as e:
                log("[ue-cov] zone %s ERR %s" % (label[:20], str(e)[:55]))
    log("[ue-cov] DONE — %d new geo'd stores across %d zones" % (tot, len(zones)))
    return tot


def _grab_feed(r, feeds):
    try:
        if "/_p/api/getFeedV1" in r.url and _ok(r):
            feeds.append(r.json())
    except Exception:
        pass


# ── per-store pull via in-session NAVIGATION — getStoreV1 fires natively (no 403), getMenuItemV1 replays ───────
def _pull_nav(w, p, captured, uuid, name, href, enrich, max_items_enrich, site, log):
    """In the warmed session, navigate to the store URL (getStoreV1 fires natively — replaying it for other
    stores 403s), parse the catalog + store outlet, then replay getMenuItemV1 per item for UPC/recipe detail.
    Returns (merged_items|None, outlet|None)."""
    captured["store"].clear(); captured["items"].clear()
    try:
        with p.expect_response(lambda r: "/_p/api/getStoreV1" in r.url, timeout=30000) as ri:
            p.goto(href, wait_until="commit", timeout=45000)
        try:
            captured["store"].append(ri.value.json())    # reliable body (held by expect_response)
        except Exception:
            pass
    except Exception:
        try: p.goto(href, wait_until="domcontentloaded", timeout=30000)
        except Exception: pass
    p.wait_for_timeout(1800); ue._clear_challenge(p, log)
    got = ue._items_from_store(captured["store"], uuid, name)
    outlet = _store_outlet(captured["store"], uuid, name, site)
    if not got:
        return None, outlet
    # flag bev-alc from the CATALOG (name match; dedicated liquor store → whole catalog is bev). The full catalog
    # is always LANDED; we only spend getMenuItemV1 on the bev-alc items — a ~20-item enrich on a 500-item grocery
    # instead of all 500 (the master only needs UPC/recipe on the beverage items). That's the item-speed lever.
    liquor = bool(ue._LIQUOR_STORE_RE.search(name))
    for it in got:
        it["is_alcohol"] = it.get("is_alcohol") or liquor or bool(ue._BEVALC_RE.search(it.get("name", "")))
    enriched = {}
    if enrich:
        targets = got if liquor else [r for r in got if r["is_alcohol"]]
        if targets:
            idx = ue._catalog_index(captured["store"])
            if captured["mi_req"] is None:                   # learn the getMenuItemV1 template once (first store)
                ue._click_first_item(p, log); p.wait_for_timeout(2000)
            req = captured["mi_req"]
            if req and req.get("body"):
                first = targets[0]
                known = {"url": req["url"], "index": idx, "headers": req.get("headers", {}),
                         "item": first.get("item_uuid"), "section": first.get("section"),
                         "subsection": first.get("subsection"), "store": uuid}
                try:
                    enriched = ue.enrich_store(w, uuid, name, targets, req["body"], known,
                                               max_items=max_items_enrich, log=log)
                except Exception as e:
                    log("  enrich %s: %s" % (name[:20], str(e)[:40]))
        for det in captured["items"]:
            data = ue._menu_item_data(det)
            if data and data.get("uuid"):
                enriched[data["uuid"]] = ue.parse_item(data, uuid, name)
    merged = [enriched.get(r["item_uuid"], r) for r in got]
    return merged, outlet


# ── STORE-DRIVEN deep crawl — iterate coverage's store list (no feed scroll); parallel-safe via per-worker proxy ─
def crawl_stores(zones, site="ubereats", shard=None, enrich=True, max_items_enrich=25, log=print):
    """DEEP crawl driven by the stores COVERAGE already found (<site>_stores) instead of re-scrolling feeds. For
    each zone: warm the session there (getStoreV1 is zone-bound) then `goto` each captured store within ~25km and
    pull its catalog + bev-alc detail. No feed extraction. PARALLEL-SAFE: each worker pins its OWN sticky proxy IP
    (via its --shard tag), so concurrent workers don't share/flag one IP → scale throughput. Resumable + deduped.
    """
    cfg = ue.SITES.get(site, ue.SITES["ubereats"])
    ue._CUR.update(base=cfg["base"], domain=cfg["domain"], source=site)
    # per-worker sticky proxy IP — the whole point: concurrent workers each on a distinct, stable residential IP.
    if os.environ.get("UE_PROXY") == "1" and resi.enabled():
        tag = "ued" + (shard.replace("/", "_") if shard else "0")
        os.environ.update(resi.sticky(tag))                  # pin RESI_PROXY_* to this worker's IP
        os.environ["BROWSER_PROXY"] = resi._session_url(tag) or ""
        log("[ue-deep] worker on sticky proxy IP tag=%s" % tag)
    else:
        os.environ.pop("BROWSER_PROXY", None)
    zonelist = zones
    if shard:
        i, n = (int(x) for x in shard.split("/")); zonelist = zones[i::n]
    allstores = warehouse.query("%s_stores" % site,
                                "SELECT store_uuid, store_name, url, lat, lng FROM t WHERE url <> '' AND lat IS NOT NULL")
    done = landed_store_uuids(site)
    log("[ue-deep] site=%s | %d zones (shard) | %d coverage stores | %d already deep-crawled"
        % (site, len(zonelist), len(allstores), len(done)))
    tot_stores = tot_items = 0
    with browser_warm.Warmer(cfg["domain"], channel="chrome", headful=True) as w:
        ctx = w._ctx; p = w._page()
        captured = {"store": [], "items": [], "mi_req": None, "sv_req": None}

        def _on(r):
            try:
                if "/_p/api/getMenuItemV1" in r.url and _ok(r):
                    captured["items"].append(r.json())
            except Exception:
                pass
        ctx.on("response", _on)

        def on_req(r):
            if "/_p/api/getMenuItemV1" in r.url and captured["mi_req"] is None:
                try:
                    captured["mi_req"] = {"url": r.url, "body": json.loads(r.post_data or "{}"),
                                          "headers": {k: v for k, v in dict(r.headers).items()
                                                      if k.lower().startswith("x-uber-") or k.lower() == "x-csrf-token"}}
                except Exception:
                    captured["mi_req"] = {"url": r.url, "body": {}, "headers": {}}
        ctx.on("request", on_req)

        for zi, z in enumerate(zonelist):
            det = geocode(z, session="z%d" % zi)
            if not det:
                log("[ue-deep] zone '%s' geocode FAILED" % z); continue
            zlat, zlng = det["latitude"], det["longitude"]
            near = [r for r in allstores
                    if abs(r["lat"] - zlat) < 0.3 and abs(r["lng"] - zlng) < 0.3 and r["store_uuid"] not in done]
            near.sort(key=lambda r: 0 if (ue._LIQUOR_STORE_RE.search(r["store_name"] or "")
                                          or ue._RETAIL_RE.search(r["store_name"] or "")) else 1)
            if not near:
                log("[ue-deep] zone %d/%d %s: no fresh coverage stores" % (zi + 1, len(zonelist), det["address"]["title"][:20])); continue
            # warm the session at this zone (a store's getStoreV1 only returns in-zone) so goto(store) works
            try:
                p.goto(pl_url(det, base=cfg["base"]), wait_until="domcontentloaded", timeout=60000)
                p.wait_for_timeout(4000); ue._clear_challenge(p, log)
            except Exception:
                pass
            log("[ue-deep] zone %d/%d %-20s: %d stores to pull" % (zi + 1, len(zonelist), det["address"]["title"][:20], len(near)))
            batch = []
            for si, r in enumerate(near):
                done.add(r["store_uuid"])
                try:
                    merged, _ = _pull_nav(w, p, captured, r["store_uuid"], r["store_name"], r["url"],
                                          enrich, max_items_enrich, site, log)
                except Exception as e:
                    merged = None; log("  %-26s ERR %s" % ((r["store_name"] or "")[:26], str(e)[:40]))
                if merged:
                    batch.extend(merged); tot_stores += 1
                if len(batch) >= 150:
                    ue.land(batch, zone=det["address"]["title"][:40], site=site, log=log); tot_items += len(batch); batch = []
            if batch:
                ue.land(batch, zone=det["address"]["title"][:40], site=site, log=log); tot_items += len(batch)
        log("[ue-deep] DONE — %d stores, %d items across %d zones" % (tot_stores, tot_items, len(zonelist)))
    return tot_stores, tot_items


# ── national crawl loop (feed-driven; used for coverage-first discovery) ──────────────────────────────────────
def crawl_zones(zones, site="ubereats", max_stores=300, enrich=True, max_items_enrich=200,
                bevalc_only=False, resume=True, log=print):
    """zones = iterable of query strings (cities/ZIPs) OR pre-geocoded pl= URLs. Crawl each zone's stores;
    dedup across zones; land full capture per store. bevalc_only filters the feed to retail/liquor merchants
    (off-premise); leave False to also capture on-premise (restaurant/bar) drink menus + recipes."""
    cfg = ue.SITES.get(site, ue.SITES["ubereats"])
    ue._CUR.update(base=cfg["base"], domain=cfg["domain"], source=site)
    done = landed_store_uuids(site) if resume else set()
    log("[ue-crawl] site=%s | %d zones | %d stores already landed (resume)" % (site, len(zones), len(done)))

    # UberEats/Postmates were cracked from our OWN home residential IP (no proxy needed) — a flagged proxy IP
    # degrades the feed (renders ~1 merchant). So default to the HOME IP; opt into the proxy only with UE_PROXY=1.
    if os.environ.get("UE_PROXY") == "1" and resi.enabled():
        os.environ["BROWSER_PROXY"] = resi._session_url("uecrawl") or ""
    else:
        os.environ.pop("BROWSER_PROXY", None)
    tot_items = tot_stores = 0
    with browser_warm.Warmer(cfg["domain"], channel="chrome", headful=True) as w:
        ctx = w._ctx; p = w._page()
        captured = {"store": [], "items": [], "mi_req": None, "sv_req": None}

        def _on_resp(r):
            # getStoreV1 is captured via expect_response in _pull_nav (reliable body); here only opportunistically
            # grab getMenuItemV1 bodies fired by clicks. Guard r.json() — bodies get evicted on navigation.
            try:
                if "/_p/api/getMenuItemV1" in r.url and _ok(r):
                    captured["items"].append(r.json())
            except Exception:
                pass
        ctx.on("response", _on_resp)

        def _hdrs(r):
            return {k: v for k, v in dict(r.headers).items()
                    if k.lower().startswith("x-uber-") or k.lower() == "x-csrf-token"}

        def on_req(r):
            # learn the app's own getStoreV1 + getMenuItemV1 request shapes (url + x-uber-* headers) once — then we
            # REPLAY getStoreV1 for every store in the zone (no per-store click) and getMenuItemV1 for every item.
            if "/_p/api/getStoreV1" in r.url and captured["sv_req"] is None:
                try: captured["sv_req"] = {"url": r.url, "headers": _hdrs(r)}
                except Exception: captured["sv_req"] = {"url": r.url, "headers": {}}
            if "/_p/api/getMenuItemV1" in r.url and captured["mi_req"] is None:
                try:
                    captured["mi_req"] = {"url": r.url, "body": json.loads(r.post_data or "{}"), "headers": _hdrs(r)}
                except Exception:
                    captured["mi_req"] = {"url": r.url, "body": {}, "headers": {}}
        ctx.on("request", on_req)

        for zi, z in enumerate(zones):
            url = z if z.startswith("http") else None
            label = z
            if url is None:
                det = geocode(z, session="z%d" % zi)
                if not det:
                    log("[ue-crawl] zone %d/%d '%s': geocode FAILED — skip" % (zi + 1, len(zones), z)); continue
                url = pl_url(det, base=cfg["base"]); label = det["address"]["title"]
            try:
                # load the feed, RETRYING the zone if it renders empty (challenge didn't clear / slow render)
                stores = []
                for attempt in range(3):
                    p.goto(url, wait_until="domcontentloaded", timeout=60000)
                    p.wait_for_timeout(4000); ue._clear_challenge(p, log)
                    for _ in range(15):                  # wait for the feed to populate (poll ~30s)
                        if len(ue._feed_stores(p)) > 5:
                            break
                        ue._clear_challenge(p, log); p.wait_for_timeout(2000)
                    w.human()
                    prev = 0
                    for i in range(80):                  # scroll the infinite feed to load EVERY merchant
                        p.mouse.wheel(0, 9000); p.wait_for_timeout(1100)
                        n = len(ue._feed_stores(p))
                        if n == prev and i > 6:
                            break
                        prev = n
                    stores = ue._feed_stores(p)
                    if len(stores) > 5:
                        break
                    log("[ue-crawl] zone %s: feed rendered %d — retry %d" % (label[:20], len(stores), attempt + 1))
                if bevalc_only:
                    stores = [s for s in stores if ue._RETAIL_RE.search(s[2]) or ue._LIQUOR_STORE_RE.search(s[2])]
                fresh = [s for s in stores if s[1] not in done]
                log("[ue-crawl] zone %d/%d %s: %d merchants, %d new" % (zi + 1, len(zones), label[:24], len(stores), len(fresh)))
                fresh = fresh[:max_stores]
                zone_items, zone_outlets = [], []
                # navigate to each store IN-SESSION (goto works inside a warmed session; getStoreV1 fires natively —
                # replaying it for arbitrary stores 403s). getMenuItemV1 then replays for the UPC/recipe detail.
                for si, (slug, uuid, name, href) in enumerate(fresh):
                    done.add(uuid)
                    merged, outlet = (None, None)
                    try:
                        merged, outlet = _pull_nav(w, p, captured, uuid, name, href, enrich, max_items_enrich, site, log)
                    except Exception as e:
                        log("  %-30s ERR %s" % (name[:30], str(e)[:50]))
                    if outlet:
                        zone_outlets.append(outlet)
                    if merged:
                        zone_items.extend(merged); tot_stores += 1
                        log("  %-30s %d items (%d w/UPC)%s" % (name[:30], len(merged),
                            sum(1 for x in merged if x.get("upc")), " @geo" if outlet else ""))
                    if (si + 1) % 20 == 0 and zone_items:                 # land in batches for very large zones
                        ue.land(zone_items, zone=label[:40], site=site, log=log); tot_items += len(zone_items); zone_items = []
                if zone_outlets:
                    warehouse.write_accumulate("%s_stores" % site, zone_outlets,
                                               key=lambda r: r["store_uuid"], fields=STORE_FIELDS)
                if zone_items:
                    ue.land(zone_items, zone=label[:40], site=site, log=log)   # land per ZONE (incremental)
                    tot_items += len(zone_items)
            except Exception as e:
                log("[ue-crawl] zone %s ERR %s" % (label[:24], str(e)[:60]))
        log("[ue-crawl] DONE — %d stores, %d items landed across %d zones" % (tot_stores, tot_items, len(zones)))
    return tot_stores, tot_items


def _ok(r):
    try: return r.status == 200
    except Exception: return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="National UberEats bev-alc crawler (geocode -> feed -> store -> item).")
    ap.add_argument("--zones", default="", help="';'-separated city/ZIP queries (or pl= URLs)")
    ap.add_argument("--zones-file", default="", help="file with one zone query per line")
    ap.add_argument("--site", default="ubereats", choices=["ubereats", "postmates"])
    ap.add_argument("--max-stores", type=int, default=300, help="max NEW stores per zone")
    ap.add_argument("--max-items-enrich", type=int, default=200)
    ap.add_argument("--no-enrich", action="store_true", help="catalog only; skip getMenuItemV1 UPC/recipe detail")
    ap.add_argument("--bevalc-only", action="store_true", help="off-premise only (retail/liquor); default also on-premise")
    ap.add_argument("--coverage", action="store_true",
                    help="FAST: harvest every merchant's geo from the feed (no per-store nav) → fills the map")
    ap.add_argument("--deep-stores", action="store_true",
                    help="DEEP crawl driven by coverage's store list (no feed scroll); per-worker sticky proxy IP")
    ap.add_argument("--shard", default="", help="i/N — process only zone slice i of N (parallel workers)")
    ap.add_argument("--no-resume", action="store_true")
    a = ap.parse_args(argv)
    zones = []
    if a.zones_file:
        zones += [ln.strip() for ln in open(a.zones_file) if ln.strip() and not ln.startswith("#")]
    if a.zones:
        zones += [z.strip() for z in a.zones.split(";") if z.strip()]
    if not zones:
        print("no zones — pass --zones or --zones-file"); return 2
    if a.shard:                                              # deal zones round-robin across N parallel workers
        i, n = (int(x) for x in a.shard.split("/"))
        zones = zones[i::n]
        print("[ue-crawl] shard %d/%d → %d zones" % (i, n, len(zones)))
    if a.coverage:
        crawl_coverage(zones, site=a.site, resume=not a.no_resume)
    elif a.deep_stores:
        crawl_stores(zones, site=a.site, shard=a.shard or None, enrich=not a.no_enrich,
                     max_items_enrich=a.max_items_enrich)
    else:
        crawl_zones(zones, site=a.site, max_stores=a.max_stores, enrich=not a.no_enrich,
                    max_items_enrich=a.max_items_enrich, bevalc_only=a.bevalc_only, resume=not a.no_resume)
    return 0


if __name__ == "__main__":
    sys.exit(main())
