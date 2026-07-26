#!/usr/bin/env python3
"""aggregator_geo.py — geo for the aggregator outlets that have NO address (doordash/ubereats/postmates).

The coverage map can't place these: their src_outlets rows carry name+city (from the sitemap slug) but no
street address and no lat/lng, so the Census geocoder can't touch them. Their store PAGE has what's missing —
so we fetch it $0 (curl_cffi Safari + the ISP pool) and:
  - DoorDash: pull the street address (RSC) → the `geocode` pass then Census-geo's it,
  - UberEats/Postmates: pull PRECISE lat/lng (+ address) straight off the schema.org block.
Writes back to src_outlets (accumulate by source|store_id). Every fetched row is marked addr_valid='agg' so a
page with nothing usable isn't re-fetched forever — the pass drains the ~1.1M no-address aggregator pool run
over run. Bounded (AGG_GEO_LIMIT) + concurrent. NOTE: this is a large crawl; it chips away, it doesn't finish
in one run. A city-centroid approximation is the fast-but-fuzzy alternative if exact dots aren't required.
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import refresh_fast
import outlet_ident


def _fetch(url, tries=3):
    """$0 fetch — curl_cffi Safari-17 through the ISP pool (rotating exit)."""
    import resi
    try:
        from curl_cffi import requests as cr
    except Exception:
        return ""
    for a in range(tries):
        u = resi.isp_url() if resi.isp_enabled() else None
        px = {"http": u, "https": u} if u else None
        try:
            r = cr.get(url, impersonate="safari17_0", proxies=px, timeout=45)
            if r.status_code == 200 and len(r.text) > 1000:
                return r.text
        except Exception:
            pass
        time.sleep(1 + a)
    return ""


def _ue_slug_map(sites=("ubereats", "postmates")):
    """{store_uuid -> slug} from the *_sitemap tables — the UberEats/Postmates store URL needs the slug."""
    out = {}
    for site in sites:
        try:
            for r in warehouse.query("%s_sitemap" % site, "SELECT store_uuid, slug FROM t"):
                if r.get("store_uuid"):
                    out[str(r["store_uuid"])] = r.get("slug") or ""
        except Exception:
            pass
    return out


def enrich_geo(limit=None, workers=None, log=print):
    limit = limit if limit is not None else int(os.environ.get("AGG_GEO_LIMIT", "800"))
    workers = workers or int(os.environ.get("AGG_GEO_WORKERS", "8"))
    rows = warehouse.query(
        "src_outlets",
        "SELECT * FROM t WHERE lat IS NULL AND (address IS NULL OR address = '') "
        "AND source IN ('doordash', 'ubereats', 'postmates') "
        "AND (addr_valid IS NULL OR addr_valid <> 'agg') LIMIT %d" % limit)
    if not rows:
        log("[agg-geo] no un-geocoded no-address aggregator outlets")
        return 0
    slugs = _ue_slug_map() if any(r["source"] in ("ubereats", "postmates") for r in rows) else {}
    out, cnt, lock = [], [0], threading.Lock()

    def _work(r):
        sid, src = str(r["store_id"]), r["source"]
        d = dict(r)
        d["addr_valid"] = "agg"                                  # mark tried (whether or not we find anything)
        try:
            if src == "doordash":
                h = _fetch("https://www.doordash.com/store/%s" % sid)
                if h:
                    e = outlet_ident.extract_doordash(h)
                    if e.get("street"):
                        d["address"] = e["street"]
                        d["city"] = e.get("city") or d.get("city") or ""
                        d["state"] = e.get("state") or d.get("state") or ""
            else:
                slug = slugs.get(sid, "")
                base = "https://www.ubereats.com" if src == "ubereats" else "https://postmates.com"
                h = _fetch("%s/store/%s/%s" % (base, slug, sid)) if slug else ""
                if h:
                    e = outlet_ident.extract_ubereats(h)
                    if e.get("lat") is not None:
                        d["lat"], d["lng"] = e["lat"], e["lng"]   # UberEats page carries PRECISE geo
                    if e.get("street"):
                        d["address"] = e["street"]
                        d["city"] = e.get("city") or d.get("city") or ""
                        d["state"] = e.get("state") or d.get("state") or ""
        except Exception:
            pass
        with lock:
            out.append({k: d.get(k) for k in refresh_fast.FLD})
            cnt[0] += 1
            if cnt[0] % 100 == 0:
                log("  [agg-geo] %d/%d" % (cnt[0], len(rows)))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_work, rows))
    warehouse.write_accumulate("src_outlets", out, key=lambda r: (r["source"], r["store_id"]),
                               fields=refresh_fast.FLD)
    got_addr = sum(1 for o in out if o.get("address"))
    got_geo = sum(1 for o in out if o.get("lat") is not None)
    log("[agg-geo] +%d address (→ geocode pass), +%d direct geo of %d fetched -> src_outlets"
        % (got_addr, got_geo, len(out)))
    return len(out)


def run(log=print):
    return enrich_geo(log=log)


if __name__ == "__main__":
    print("enriched %d aggregator outlets" % enrich_geo())
