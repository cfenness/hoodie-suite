#!/usr/bin/env python3
"""ubereats_sitemap.py — the $0 US UberEats store universe, from UberEats' own (gzipped) sitemaps.

robots.txt advertises `sitemap-index-<hash>.xml.gz` → ~121 gzipped `sitemap-store-<hash>-NNN.xml.gz`, ~50k
store URLs each (GLOBAL — we keep only US: `ubereats.com/store/<slug>/<id>` with no locale prefix; ~11k US
per sub → ~1.3M US stores). Harvested $0 (curl_cffi Safari-17 + the flat ISP pool, gunzip), no geo pins, no
headful — the discovery FEED needs a warmed session, but the published sitemap does not. This is a source
layer for the outlet pre-master (outlet_union); UberEats store PAGES fetch $0 too (address + phone present),
so its outlets strong-match DoorDash/Toast once enriched.

Postmates is Uber-owned and its inventory is subsumed by UberEats — no separate harvestable sitemap.

Lands `ubereats_outlets` (accumulate, key=store_id).
    python ubereats_sitemap.py               # all US
    python ubereats_sitemap.py --subs 3      # bounded (smoke)
"""
import argparse
import gzip
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

ROBOTS = "https://www.ubereats.com/robots.txt"
_LOC = re.compile(r"<loc>([^<]+)</loc>")
# US store = no locale segment between .com and /store (international is /<locale>/store/...)
_US_STORE = re.compile(r"https://www\.ubereats\.com/store/([^/\"]+)/([A-Za-z0-9_-]{20,24})")
OUTLET_FIELDS = ["store_id", "name", "slug", "url", "source"]


def _get(url, tries=4, gzipped=False, log=print):
    """$0 fetch — curl_cffi Safari-17 through the ISP pool. Returns text ('' on failure). gunzips when asked."""
    import resi
    try:
        from curl_cffi import requests as cr
    except Exception as e:
        log("  [ue-sitemap] curl_cffi unavailable: %s" % str(e)[:60])
        return ""
    last = ""
    for a in range(tries):
        u = resi.isp_url() if resi.isp_enabled() else None
        px = {"http": u, "https": u} if u else None
        try:
            r = cr.get(url, impersonate="safari17_0", proxies=px, timeout=45)
            if r.status_code == 200:
                if gzipped:
                    try:
                        return gzip.decompress(r.content).decode("utf-8", "replace")
                    except Exception:
                        return r.text                      # already-decompressed by the transport
                if len(r.text) > 80:
                    return r.text
            last = "status %s" % r.status_code
        except Exception as e:
            last = str(e)[:70]
        time.sleep(1 + a)
    log("  [ue-sitemap] fetch failed after %d (%s): %s" % (tries, last, url[:70]))
    return ""


def _index_url(log=print):
    robots = _get(ROBOTS, log=log)
    for m in re.finditer(r"(?i)sitemap:\s*(\S+)", robots):
        if "sitemap-index" in m.group(1):
            return m.group(1)
    return None


def harvest(subs_limit=None, cap=None, log=print):
    idx_url = _index_url(log=log)
    if not idx_url:
        log("[ue-sitemap] no sitemap-index in robots — nothing harvested")
        return []
    idx = _get(idx_url, gzipped=idx_url.endswith(".gz"), log=log)
    subs = [u for u in _LOC.findall(idx) if "store" in u]
    if subs_limit:
        subs = subs[:subs_limit]
    log("[ue-sitemap] %d store sub-sitemaps" % len(subs))
    rows, seen = [], set()
    for si, sub in enumerate(subs):
        body = _get(sub, gzipped=sub.endswith(".gz"), log=log)
        if not body:
            continue
        n = 0
        for slug, sid in _US_STORE.findall(body):
            if sid in seen:
                continue
            seen.add(sid)
            rows.append(dict(store_id=sid, name=slug.replace("-", " ").strip()[:90], slug=slug,
                             url="https://www.ubereats.com/store/%s/%s" % (slug, sid), source="ubereats"))
            n += 1
            if cap and n >= cap:
                break
        log("  sub %d/%d: %d US stores" % (si + 1, len(subs), n))
        if si % 10 == 9 and rows:                          # checkpoint periodically (big harvest)
            warehouse.write_accumulate("ubereats_outlets", rows, key=lambda r: r["store_id"], fields=OUTLET_FIELDS)
    if rows:
        warehouse.write_accumulate("ubereats_outlets", rows, key=lambda r: r["store_id"], fields=OUTLET_FIELDS)
        log("[ue-sitemap] landed %d US stores -> ubereats_outlets" % len(rows))
    else:
        log("[ue-sitemap] no US stores harvested (fetch blocked?) — nothing landed")
    return rows


def run(log=print):
    return harvest(log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Harvest the US UberEats store universe from its sitemaps ($0).")
    ap.add_argument("--subs", type=int, default=None, help="cap store sub-sitemaps (smoke)")
    ap.add_argument("--cap", type=int, default=None, help="cap US stores per sub")
    a = ap.parse_args(argv)
    rows = harvest(subs_limit=a.subs, cap=a.cap)
    print("harvested %d US stores" % len(rows))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
