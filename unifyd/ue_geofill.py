#!/usr/bin/env python3
"""ue_geofill.py — put the FULL UberEats/Postmates universe on the map WITHOUT the proxy or geocode-guessing.

Every /store/ page embeds a JSON-LD `Restaurant` block with EXACT `geo` (lat/lng) + a full PostalAddress
(street/city/zip) + phone + priceRange + cuisine. The pages are public + robots-permitted, so we fetch them
DIRECT (home IP, curl_cffi Chrome-TLS) — no residential proxy, no Census address-matching. We stream each page
and STOP after the JSON-LD (~125KB of 606KB, ~80% less bandwidth). Resumable: skips uuids already captured.

Lands `<site>_geo` (store_uuid + geo + address + phone/cuisine); refresh_fast merges it into src_outlets so the
coverage map fills in. Block-aware: non-200 / missing JSON-LD → paced retry, then a residential-proxy fallback;
404/410 → geo_status='gone' (won't retry). stdlib + curl_cffi.

  python ue_geofill.py ubereats            # geofill all un-geo'd ubereats outlets
  python ue_geofill.py ubereats 5000 24    # cap 5000 this run, 24 workers
"""
import html
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resi
import warehouse

FIELDS = ["store_uuid", "store_name", "lat", "lng", "street", "city", "state", "postal_code",
          "phone", "price_range", "cuisine", "geo_status", "captured_at"]
_JSONLD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_MERGE = threading.Lock()


def _fetch(url, px=None):
    """Fetch the store page (plain, non-streaming — curl_cffi's streaming aborts under threads; the wire is
    gzipped so full-page bandwidth is ~120KB). Returns (status, text). 429/403 = velocity block (retry)."""
    from curl_cffi import requests as cr
    r = cr.Session(impersonate="chrome", timeout=45,
                   proxies={"http": px, "https": px} if px else None).get(url)
    return r.status_code, (r.text if r.status_code == 200 else "")


def parse(doc):
    """Pull the Restaurant/FoodEstablishment JSON-LD → geo + address dict, or None if absent."""
    for b in _JSONLD.findall(doc):
        try:
            d = json.loads(b)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        g, a = d.get("geo"), d.get("address")
        if not (isinstance(g, dict) and g.get("latitude") is not None):
            continue
        a = a if isinstance(a, dict) else {}
        cui = d.get("servesCuisine")
        u = lambda s: html.unescape(s) if isinstance(s, str) else (s or "")
        return {"store_name": u(d.get("name")), "lat": float(g["latitude"]), "lng": float(g["longitude"]),
                "street": u(a.get("streetAddress")), "city": u(a.get("addressLocality")),
                "state": u(a.get("addressRegion")), "postal_code": u(a.get("postalCode")),
                "phone": d.get("telephone") or "", "price_range": d.get("priceRange") or "",
                "cuisine": u(", ".join(cui)) if isinstance(cui, list) else u(cui)}
    return None


def _base(site):
    return "https://www.ubereats.com" if site == "ubereats" else "https://postmates.com"


def _targets(site):
    """store_uuid+url still needing geo, minus those already terminal in <site>_geo. Universe = <site>_sitemap
    (full harvest, has url) if present, else <site>_stores (coverage — build the /store/ url from slug+uuid)."""
    try:
        done = {r["store_uuid"] for r in warehouse.query("%s_geo" % site,
                "SELECT store_uuid FROM t WHERE geo_status IN ('ok','gone')")}
    except Exception:
        done = set()
    try:
        rows = warehouse.query("%s_sitemap" % site, "SELECT store_uuid, url, store_name FROM t WHERE url <> ''")
    except Exception:
        rows = []
    if not rows:                       # no sitemap harvest for this site — fall back to the crawled stores,
        rows = [r for r in warehouse.query("%s_stores" % site,   # which already carry a resolvable /store/ url
                "SELECT store_uuid, url, store_name FROM t WHERE store_uuid <> '' AND url IS NOT NULL AND url <> ''")]
    return [r for r in rows if r["store_uuid"] not in done]


def wait_clear(site="ubereats", probe_every=120, max_min=360, log=print):
    """Before opening the pool, confirm the home IP isn't velocity-clamped: one slow probe every `probe_every`s
    until a 200 (or timeout). Avoids hammering — and thereby prolonging — a 429 soft-ban."""
    try:
        u = warehouse.query("%s_sitemap" % site, "SELECT url FROM t WHERE url <> '' LIMIT 1")[0]["url"]
    except Exception:
        return True
    t0 = time.time()
    while time.time() - t0 < max_min * 60:
        try:
            st, doc = _fetch(u)
        except Exception:
            st, doc = 0, ""
        if st == 200 and parse(doc):
            log("[geofill] home IP clear (200) after %dm — starting" % int((time.time() - t0) / 60))
            return True
        log("[geofill] home IP still clamped (%s); waiting %ds…" % (st, probe_every))
        time.sleep(probe_every)
    log("[geofill] gave up waiting for clear after %dm" % max_min)
    return False


def geofill(site="ubereats", limit=None, workers=4, flush=1000, log=print, preflight=True):
    # ISP pool = fresh un-flagged IPs → no need to wait out a home-IP velocity clamp; go straight in.
    if preflight and not resi.isp_enabled() and not wait_clear(site, log=log):
        return 0
    tgt = _targets(site)
    if limit:
        tgt = tgt[:limit]
    total = len(tgt)
    log("[geofill] %s: %d outlets to geo (workers=%d)" % (site, total, workers))
    if not total:
        return 0
    out, done, ok, gone, blocked = [], [0], [0], [0], [0]
    from collections import deque
    recent = deque(maxlen=40)                 # rolling block outcomes → circuit breaker
    go = threading.Event(); go.set()          # cleared = paused (IP re-flagged, let it cool)
    t0 = time.time()

    isp = resi.isp_enabled()
    if isp:
        log("[geofill] routing via ISP pool (%d IPs, flat-rate/unlimited)" % len(resi.isp_pool()))

    def work(r):
        go.wait()                             # park here while paused
        url, uuid = r["url"], r["store_uuid"]
        for attempt in range(4):
            # ISP pool (flat-rate): rotate to a fresh pool IP each attempt — spreads load, dodges per-IP flags.
            # No pool: home IP direct, escalating to rotating residential only on the last try.
            if isp:
                px = resi.isp_url("geo%s%d" % (uuid[:6], attempt))
            else:
                px = resi._session_url("geo%s%d" % (uuid[:6], attempt)) if attempt >= 3 else None
            try:
                st, doc = _fetch(url, px)
            except Exception:
                st, doc = 0, ""
            if st in (404, 410):
                return {"store_uuid": uuid, "store_name": r.get("store_name") or "", "lat": None, "lng": None,
                        "street": "", "city": "", "state": "", "postal_code": "", "phone": "", "price_range": "",
                        "cuisine": "", "geo_status": "gone", "captured_at": int(time.time())}
            if st == 200:
                p = parse(doc)
                if p:
                    p.update(store_uuid=uuid, geo_status="ok", captured_at=int(time.time()))
                    return p
            # 429/403 = velocity clamp — back off harder and longer each attempt
            time.sleep((6 if st in (429, 403) else 1.5) * (attempt + 1))
        return None                                # blocked / no JSON-LD after retries — leave for a later run

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, r): r for r in tgt}
        for f in as_completed(futs):
            done[0] += 1
            res = f.result()
            if res is None:
                blocked[0] += 1
            else:
                (gone if res["geo_status"] == "gone" else ok)[0] += 1
                with _MERGE:
                    out.append(res)
                    if len(out) >= flush:
                        warehouse.write_accumulate("%s_geo" % site, out,
                                                   key=lambda r: r["store_uuid"], fields=FIELDS)
                        out = []
            # circuit breaker: if the IP re-flags (challenge/block spike), pause everyone + wait for real recovery
            recent.append(res is None)
            if go.is_set() and len(recent) == recent.maxlen and sum(recent) >= 0.6 * recent.maxlen:
                go.clear()
                log("  [breaker] %d%% blocked — pausing to let the IP cool" % int(100 * sum(recent) / len(recent)))
                wait_clear(site, log=log)
                recent.clear(); go.set()
            if done[0] % 1000 == 0 or done[0] == total:
                rate = done[0] / max(1, time.time() - t0)
                log("  %d/%d  ok=%d gone=%d blocked=%d  %.1f/s  ETA %dm"
                    % (done[0], total, ok[0], gone[0], blocked[0], rate,
                       int((total - done[0]) / max(0.1, rate) / 60)))
    with _MERGE:
        if out:
            warehouse.write_accumulate("%s_geo" % site, out, key=lambda r: r["store_uuid"], fields=FIELDS)
    log("[geofill] %s DONE: ok=%d gone=%d blocked=%d in %.1fmin"
        % (site, ok[0], gone[0], blocked[0], (time.time() - t0) / 60))
    return ok[0]


if __name__ == "__main__":
    import kroger_api
    kroger_api._load_creds()
    site = sys.argv[1] if len(sys.argv) > 1 else "ubereats"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    geofill(site, limit, workers)
