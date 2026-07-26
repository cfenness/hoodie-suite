#!/usr/bin/env python3
"""ue_feed_sweep.py — sub-hour NATIONAL UberEats/Postmates geo via the BULK feed, COLD and concurrent.

The per-store page fetch is ~790k requests = a multi-day crawl. getFeedV1 returns 300+ geo'd merchants per
market call (each with a mapMarker{lat,lng}), so the whole country is a few thousand feed calls, not a million
page fetches. And it runs $0/headless: ue_crawl.geocode already proves the Uber BFF answers COLD — plain
curl_cffi + a dummy `x-csrf-token: x`, no warmed browser — and the feed's location rides IN the request
(pl_url), not a session cookie. So we geocode a grid of real markets and pull each feed concurrently.

Per market: geocode(query) -> location; load the feed for that location; harvest every mapMarker via
ue_crawl._feed_outlets. Collect across all markets into one dict (keyed by the URL store id = the sitemap id),
then ONE write to <site>_stores (concurrent write_accumulate to a shared table races — so we fan-out the
fetches but serialize the single write). normalize then maps them like the deep-crawl stores.

Markets = the real (city, state) delivery markets we already know exist, ranked by store count, so we spend
calls where the stores are. Resumable (skips store ids already landed).

VERIFY-LIVE (blocked by IP right now): whether the feed JSON comes back on a COLD getFeedV1 POST vs must be
scraped from the feed-page HTML. Both paths are implemented; _feed_json picks whichever returns markers. If
neither does cold, drop in the exact getFeedV1 request captured from the browser (see _feed_json_api).
"""
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import resi
import ue_crawl
import ubereats as ue

_SCRIPT = re.compile(r"<script[^>]*>(.*?)</script>", re.S)
_OBJ = re.compile(r"\{.*\}", re.S)


def markets_from_warehouse(limit=6000):
    """Real delivery markets = distinct (city, state) across our known outlets, ranked by store count — spend
    feed calls where stores actually are. 'City, ST' strings for ue_crawl.geocode."""
    try:
        rows = warehouse.query(
            "src_outlets",
            "SELECT city, state, count(*) c FROM t WHERE city IS NOT NULL AND city <> '' "
            "AND state IS NOT NULL AND state <> '' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT %d" % limit)
    except Exception:
        return []
    return ["%s, %s" % (r["city"], r["state"]) for r in rows]


def _json_blobs(html):
    """Best-effort: pull parseable JSON objects out of the feed page's <script> tags (the SSR'd feed state).
    Reused by _feed_outlets, which walks arbitrary JSON for storeUuid+mapMarker — so we don't need the exact
    shape, just valid JSON that contains the markers."""
    out = []
    for m in _SCRIPT.finditer(html or ""):
        body = m.group(1).strip()
        if "mapMarker" not in body and "storeUuid" not in body:
            continue
        for cand in (body, (_OBJ.search(body).group(0) if _OBJ.search(body) else "")):
            if not cand:
                continue
            try:
                out.append(json.loads(cand))
                break
            except Exception:
                continue
    return out


def _feed_json_page(det, session):
    """COLD path A: GET the market's feed page and lift the embedded feed JSON (mirrors the store-page fetch
    that already works cold)."""
    from curl_cffi import requests as cr
    px = resi._session_url(session)
    s = cr.Session(impersonate="safari17_0", proxies={"http": px, "https": px} if px else None, timeout=35)
    try:
        html = s.get(ue_crawl.pl_url(det)).text
    except Exception:
        return []
    return _json_blobs(html)


def _feed_json_api(det, session):
    """COLD path B: POST getFeedV1 directly (like ue_crawl.geocode posts mapsSearchV1). Body carries the
    location. Exact field names vary by app version — this is the best-effort shape; if it returns nothing,
    paste the real getFeedV1 request captured from the browser Network tab here."""
    from curl_cffi import requests as cr
    px = resi._session_url(session)
    s = cr.Session(impersonate="safari17_0", proxies={"http": px, "https": px} if px else None, timeout=35)
    H = {"content-type": "application/json", "accept": "application/json", "x-csrf-token": "x"}
    loc = {"address": det["address"]["title"], "reference": det["reference"],
           "referenceType": det["referenceType"], "latitude": det["latitude"], "longitude": det["longitude"]}
    body = {"userQuery": "", "date": "", "startTime": 0, "endTime": 0, "carModeStr": "PICKUP",
            "pageInfo": {"offset": 0, "pageSize": 80}, "targetLocation": loc}
    try:
        s.get("https://www.ubereats.com/")
        j = s.post(ue_crawl.API + "getFeedV1", json=body, headers=H).json()
        return [j] if j else []
    except Exception:
        return []


def _feed_json(det, session):
    payloads = _feed_json_page(det, session)
    if not any(ue_crawl._feed_outlets([p], "ubereats") for p in payloads):   # page had no markers → try the API
        payloads = _feed_json_api(det, session) or payloads
    return payloads


def sweep(site="ubereats", markets=None, workers=None, resume=True, log=print):
    workers = workers or int(os.environ.get("UE_SWEEP_WORKERS", "32"))
    markets = markets or markets_from_warehouse(int(os.environ.get("UE_SWEEP_MARKETS", "6000")))
    cfg = ue.SITES.get(site, ue.SITES["ubereats"])
    ue._CUR.update(base=cfg["base"], domain=cfg["domain"], source=site)
    store_table = "%s_stores" % site
    done = ue_crawl.landed_store_uuids(site, "stores") if resume else set()
    log("[ue-sweep] site=%s | %d markets | %d workers | %d already landed" % (site, len(markets), workers, len(done)))
    out, lock, seen = [], threading.Lock(), set(done)
    prog = [0, 0]

    def _one(q):
        try:
            det = ue_crawl.geocode(q, session="sw%d" % (abs(hash(q)) % 900))
            if not det:
                return
            outlets = ue_crawl._feed_outlets(_feed_json(det, "sw%d" % (abs(hash(q)) % 900)), site)
        except Exception:
            return
        with lock:
            new = 0
            for o in outlets:
                if o["store_uuid"] and o["store_uuid"] not in seen:
                    seen.add(o["store_uuid"]); out.append(o); new += 1
            prog[0] += 1; prog[1] += new
            if prog[0] % 200 == 0:
                log("[ue-sweep] %d/%d markets · %d new geo'd stores" % (prog[0], len(markets), prog[1]))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_one, markets))
    if out:
        warehouse.write_accumulate(store_table, out, key=lambda r: r["store_uuid"], fields=ue_crawl.STORE_FIELDS)
    log("[ue-sweep] DONE — %d new geo'd %s stores from %d markets -> %s" % (len(out), site, len(markets), store_table))
    return len(out)


def run(log=print):
    n = sweep("ubereats", log=log)
    n += sweep("postmates", log=log)
    return n


if __name__ == "__main__":
    print("swept %d stores" % run())
