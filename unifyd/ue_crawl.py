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
        pid = hits[0]["id"]
        det = (s.post(API + "getDeliveryLocationV1", json={"placeId": pid, "provider": "uber_places"},
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
def landed_store_uuids(site="ubereats"):
    try:
        rows = warehouse.query("%s_products" % site, "SELECT DISTINCT store_uuid FROM t WHERE store_uuid <> ''")
        return {r["store_uuid"] for r in rows}
    except Exception:
        return set()


# ── one-store pull (mirrors ubereats.crawl's proven per-store logic) ──────────────────────────────────────────
def _pull_one_store(w, p, captured, uuid, name, enrich, max_items_enrich, log):
    captured["store"].clear(); captured["items"].clear()
    if not w.click_through(uuid):
        return None
    got = ue._items_from_store(captured["store"], uuid, name)
    if not got:
        return []
    enriched = {}
    if enrich:
        idx = ue._catalog_index(captured["store"])
        ue._click_first_item(p, log)
        p.wait_for_timeout(2200)
        req = captured["mi_req"]
        if req and req.get("body"):
            first = ue._menu_item_data(captured["items"][0]) if captured["items"] else None
            known = {"url": req["url"], "index": idx, "headers": req.get("headers", {}),
                     "item": (first or {}).get("uuid"), "section": (first or {}).get("sectionUuid"),
                     "subsection": (first or {}).get("subsectionUuid"), "store": uuid}
            enriched = ue.enrich_store(w, uuid, name, got, req["body"], known, max_items=max_items_enrich, log=log)
        for det in captured["items"]:
            data = ue._menu_item_data(det)
            if data and data.get("uuid"):
                enriched[data["uuid"]] = ue.parse_item(data, uuid, name)
    merged = [enriched.get(r["item_uuid"], r) for r in got]
    liquor = bool(ue._LIQUOR_STORE_RE.search(name))
    for it in merged:
        it["is_alcohol"] = it["is_alcohol"] or liquor or bool(ue._BEVALC_RE.search(it.get("name", "")))
    return merged


# ── national crawl loop ───────────────────────────────────────────────────────────────────────────────────────
def crawl_zones(zones, site="ubereats", max_stores=300, enrich=True, max_items_enrich=200,
                bevalc_only=False, resume=True, log=print):
    """zones = iterable of query strings (cities/ZIPs) OR pre-geocoded pl= URLs. Crawl each zone's stores;
    dedup across zones; land full capture per store. bevalc_only filters the feed to retail/liquor merchants
    (off-premise); leave False to also capture on-premise (restaurant/bar) drink menus + recipes."""
    cfg = ue.SITES.get(site, ue.SITES["ubereats"])
    ue._CUR.update(base=cfg["base"], domain=cfg["domain"], source=site)
    done = landed_store_uuids(site) if resume else set()
    log("[ue-crawl] site=%s | %d zones | %d stores already landed (resume)" % (site, len(zones), len(done)))

    # route the browser through the residential proxy (sticky per run so the session IP is stable)
    if resi.enabled():
        os.environ["BROWSER_PROXY"] = resi._session_url("uecrawl") or ""
    tot_items = tot_stores = 0
    with browser_warm.Warmer(cfg["domain"], channel="chrome", headful=True) as w:
        ctx = w._ctx; p = w._page()
        captured = {"store": [], "items": [], "mi_req": None}
        ctx.on("response", lambda r: (
            captured["store"].append(r.json()) if "/_p/api/getStoreV1" in r.url and _ok(r) else
            captured["items"].append(r.json()) if "/_p/api/getMenuItemV1" in r.url and _ok(r) else None))

        def on_req(r):
            if "/_p/api/getMenuItemV1" in r.url and captured["mi_req"] is None:
                try:
                    captured["mi_req"] = {"url": r.url, "body": json.loads(r.post_data or "{}"),
                                          "headers": {k: v for k, v in dict(r.headers).items()
                                                      if k.lower().startswith("x-uber-") or k.lower() == "x-csrf-token"}}
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
                p.goto(url, wait_until="domcontentloaded", timeout=60000)
                p.wait_for_timeout(5000); ue._clear_challenge(p, log); w.human()
                prev = 0
                for i in range(60):
                    p.mouse.wheel(0, 9000); p.wait_for_timeout(1000)
                    n = len(ue._feed_stores(p))
                    if n == prev and i > 4:
                        break
                    prev = n
                stores = ue._feed_stores(p)
                if bevalc_only:
                    stores = [s for s in stores if ue._RETAIL_RE.search(s[2]) or ue._LIQUOR_STORE_RE.search(s[2])]
                fresh = [s for s in stores if s[1] not in done]
                log("[ue-crawl] zone %d/%d %s: %d merchants, %d new" % (zi + 1, len(zones), label[:24], len(stores), len(fresh)))
                zone_items = []
                for slug, uuid, name, href in fresh[:max_stores]:
                    if "/feed" not in p.url:
                        try: p.goto(url, wait_until="commit", timeout=45000)
                        except Exception: pass
                        p.wait_for_timeout(2500); ue._clear_challenge(p, log)
                    try:
                        merged = _pull_one_store(w, p, captured, uuid, name, enrich, max_items_enrich, log)
                    except Exception as e:
                        log("  %-30s ERR %s" % (name[:30], str(e)[:50])); merged = None
                    done.add(uuid)
                    if merged:
                        zone_items.extend(merged); tot_stores += 1
                        log("  %-30s %d items (%d w/UPC)" % (name[:30], len(merged), sum(1 for x in merged if x.get("upc"))))
                    try: p.go_back(); p.wait_for_timeout(2000)
                    except Exception: pass
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
    ap.add_argument("--no-resume", action="store_true")
    a = ap.parse_args(argv)
    zones = []
    if a.zones_file:
        zones += [ln.strip() for ln in open(a.zones_file) if ln.strip() and not ln.startswith("#")]
    if a.zones:
        zones += [z.strip() for z in a.zones.split(";") if z.strip()]
    if not zones:
        print("no zones — pass --zones or --zones-file"); return 2
    crawl_zones(zones, site=a.site, max_stores=a.max_stores, enrich=not a.no_enrich,
                max_items_enrich=a.max_items_enrich, bevalc_only=a.bevalc_only, resume=not a.no_resume)
    return 0


if __name__ == "__main__":
    sys.exit(main())
