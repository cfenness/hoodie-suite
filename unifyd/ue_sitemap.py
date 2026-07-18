#!/usr/bin/env python3
"""ue_sitemap.py — capture the FULL UberEats US outlet universe from the store sitemaps (~285k US stores), not
just the ~45k that metro feeds surface. robots.txt lists 26 `sitemap-store-*.xml.gz`; every US store URL (no
country prefix) becomes an outlet (uuid + name from the slug). GEO is null here — the feed coverage crawl fills
lat/lng for the zones it visits; this is the complete ACCOUNT list (every merchant incl. grocery/chains/indies).
Lands <site>_sitemap (separate table — no race with the live coverage crawl). Merge into src_outlets via
sitemap_to_src_outlets(). Headless curl_cffi + residential proxy, ~3min.
"""
import gzip
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resi
import warehouse

SM = "https://www.ubereats.com/sitemap-store-771af823-%03d.xml.gz"     # 000..025
FIELDS = ["store_uuid", "store_name", "slug", "url", "source", "captured_at"]
_STREETY = re.compile(r"^\d|^(st|ave|blvd|rd|dr|ln|hwy|ste|suite|unit|n|s|e|w|ne|nw|se|sw)$", re.I)


def _name_from_slug(slug):
    """slug = merchant-name + street-address (e.g. 'aldi-1725-herndon-ave', 'kroger-corinth'). Take the leading
    words up to the first address-looking token."""
    out = []
    for tok in slug.split("-"):
        if out and _STREETY.match(tok):
            break
        out.append(tok)
    return (" ".join(out) or slug.replace("-", " ")).strip().title()


def pull(site="ubereats", log=print):
    base = "https://www.ubereats.com" if site == "ubereats" else "https://postmates.com"
    from curl_cffi import requests as cr
    px = resi._session_url("uesitemap")
    seen = {}
    t0 = time.time()
    failed = []
    for i in range(26):
        xml = None
        for attempt in range(4):                            # retry each sitemap on a FRESH proxy IP (the proxy has
            px = resi._session_url("sm%d_%d" % (i, attempt))  # transient CONNECT-tunnel blips)
            try:
                s = cr.Session(impersonate="chrome", proxies={"http": px, "https": px} if px else None, timeout=60)
                xml = gzip.decompress(s.get(SM % i).content).decode("utf-8", "replace")
                break
            except Exception as e:
                last = e
        if xml is None:
            failed.append(i); log("  sitemap %02d: FAILED after retries: %s" % (i, str(last)[:50])); continue
        for loc in re.findall(r"<loc>(https://www\.ubereats\.com/store/[^<]+)</loc>", xml):   # /store/ = US (no /xx/)
            m = re.search(r"/store/([^/]+)/([A-Za-z0-9_\-]{20,})$", loc)
            if m and m.group(2) not in seen:
                slug, uuid = m.group(1), m.group(2)
                seen[uuid] = {"store_uuid": uuid, "store_name": _name_from_slug(slug), "slug": slug,
                              "url": "%s/store/%s/%s" % (base, slug, uuid), "source": site,
                              "captured_at": int(time.time())}
        if i % 5 == 0 or i == 25:
            log("  sitemap %02d/25: %s US outlets cumulative (%ds)" % (i, format(len(seen), ","), int(time.time() - t0)))
    # ACCUMULATE (never overwrite) so a partial run only GROWS the universe table
    warehouse.write_accumulate("%s_sitemap" % site, list(seen.values()), key=lambda r: r["store_uuid"], fields=FIELDS)
    log("[sitemap] %s: %s US outlets this run -> %s_sitemap%s" % (site, format(len(seen), ","), site,
        (" (still failed: %s)" % failed) if failed else ""))
    return len(seen)


def sitemap_to_src_outlets(site="ubereats", log=print):
    """Merge the sitemap universe into src_outlets as accounts (name+uuid, no geo). Order matters: run this ONCE
    to seed the full account list; the geo'd coverage stores (refresh_fast) then overwrite the crawled uuids with
    lat/lng. So src_outlets ends up = full universe, geocoded where we've crawled."""
    import refresh_fast
    # only add uuids NOT already in src_outlets — so we never overwrite an already-geocoded coverage store with a
    # no-geo sitemap row (the geo'd ones stay; the sitemap adds the ~240k we haven't crawled). Preserves geo.
    try:
        have = {r["store_id"] for r in warehouse.query("src_outlets",
                "SELECT store_id FROM t WHERE source = '%s'" % site)}
    except Exception:
        have = set()
    rows = warehouse.query("%s_sitemap" % site, "SELECT store_uuid, store_name FROM t WHERE store_uuid <> ''")
    out = []
    for r in rows:
        if r["store_uuid"] in have:
            continue
        d = {k: v for k, v in zip(refresh_fast.FLD, [None] * len(refresh_fast.FLD))}
        d.update(source=site, store_id=r["store_uuid"], store_name=r["store_name"] or "", is_chain=False,
                 f_beer=False, f_wine=False, f_spirits=False, f_hemp=False, f_cannabis=False, f_rtd_spirits=False,
                 flag_basis="none", license_conflict=False, chain="", address="", city="", state="", zip="",
                 phone="", addr_valid=False, hoodie_outlet="", name_key="", phone_norm="", addr_key="",
                 geo_cell="", county_fips="", lat=None, lng=None)
        out.append(d)
    if out:
        warehouse.write_accumulate("src_outlets", out, key=lambda r: (r["source"], r["store_id"]),
                                   fields=refresh_fast.FLD)
    log("[sitemap] merged %s %s accounts into src_outlets" % (format(len(out), ","), site))
    return len(out)


if __name__ == "__main__":
    import kroger_api
    kroger_api._load_creds()
    site = sys.argv[1] if len(sys.argv) > 1 else "ubereats"
    pull(site)
    sitemap_to_src_outlets(site)
