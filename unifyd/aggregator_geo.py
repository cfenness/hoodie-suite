#!/usr/bin/env python3
"""aggregator_geo.py — PRECISE geo for UberEats/Postmates outlets, whose only geo source is the store page.

UberEats/Postmates land from the sitemap with just name+slug — no city, no address, no coords — so the fast
city-centroid layer and the Census address geocoder can't touch them; the store PAGE is the only geo source.
This fetches it $0 (curl_cffi Safari + the ISP pool) and pulls PRECISE lat/lng (+ address) straight off the
schema.org block, stamping geo_precision='exact'. A fetched-but-empty page is stamped 'agg_miss' so it isn't
re-fetched forever — the pass drains the ~790k no-address UberEats/Postmates pool run over run. Bounded
(AGG_GEO_LIMIT) + concurrent; a large crawl that chips away. (DoorDash ships city+state, so it's mapped
instantly by the city-centroid fast layer instead — see city_centroid.py.)
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
    # 800/run was a trickle against ~790k UE+PM. Bigger batch + more concurrency (the ISP pool rotates exits, so
    # this stays polite per-IP) so a daily run makes real progress; a dedicated drain can pass AGG_GEO_LIMIT huge.
    limit = limit if limit is not None else int(os.environ.get("AGG_GEO_LIMIT", "40000"))
    workers = workers or int(os.environ.get("AGG_GEO_WORKERS", "20"))
    # doordash is handled by the city-centroid fast layer (it ships city+state) — not here. ubereats/postmates
    # ship neither city nor address, only name+slug, so the store PAGE is the only geo source: fetch it. Skip
    # rows already pinned exact or already tried-empty ('agg_miss'). geo_precision (VARCHAR) is the marker —
    # NOT addr_valid, which is a BOOL (writing a string into it silently voided the whole prior write).
    gp = ("AND (geo_precision IS NULL OR geo_precision NOT IN ('exact', 'agg_miss')) "
          if warehouse.has_column("src_outlets", "geo_precision") else "")
    rows = warehouse.query(
        "src_outlets",
        "SELECT * FROM t WHERE lat IS NULL AND source IN ('ubereats', 'postmates') " + gp +
        "LIMIT %d" % limit)
    if not rows:
        log("[agg-geo] no un-geocoded no-address aggregator outlets")
        return 0
    slugs = _ue_slug_map() if any(r["source"] in ("ubereats", "postmates") for r in rows) else {}
    out, cnt, lock = [], [0], threading.Lock()

    def _work(r):
        sid, src = str(r["store_id"]), r["source"]
        d = dict(r)
        d["geo_precision"] = "agg_miss"                          # tried marker (overwritten to 'exact' on a hit)
        try:
            slug = slugs.get(sid, "")
            base = "https://www.ubereats.com" if src == "ubereats" else "https://postmates.com"
            h = _fetch("%s/store/%s/%s" % (base, slug, sid)) if slug else ""
            if h:
                e = outlet_ident.extract_ubereats(h)
                if e.get("lat") is not None:
                    d["lat"], d["lng"] = e["lat"], e["lng"]       # UberEats page carries PRECISE geo
                    d["geo_precision"] = "exact"
                if e.get("street"):                           # getStoreV1 location = full street address
                    d["address"] = e["street"]
                    d["city"] = e.get("city") or d.get("city") or ""
                    d["state"] = e.get("state") or d.get("state") or ""
                    d["zip"] = e.get("zip") or d.get("zip") or ""
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
