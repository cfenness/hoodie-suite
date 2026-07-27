#!/usr/bin/env python3
"""doordash_sitemap.py — the $0 national DoorDash store universe, straight from DoorDash's OWN sitemaps.

robots.txt advertises `sitemap-store-doordash-index.xml` → ~130 per-state sub-sitemaps
(`sitemap-doordash-<st>-stores.xml`), each ~9–10k store URLs of the form
`/store/<name-slug>-<store_id>/`. We harvest every store id + name-slug + state at ZERO cost — curl_cffi
Safari-17 TLS impersonation through the flat-rate residential ISP pool (the same path that fetches the
menus), no geo pins, no BD Browser, no Google Maps. This replaces the metered `Proxy.setLocation` grid /
BD-Browser discovery: DoorDash publishes the whole store list, so we just read it.

This is the discovery SPINE that feeds doordash_naop (on-premise restaurant menus) and the retail
connectors nationally. Lands `doordash_stores` (accumulate, key=store_id) — the same table
doordash_discover fans from.

    python doordash_sitemap.py                 # every state
    python doordash_sitemap.py --states or,fl  # bounded
    python doordash_sitemap.py --cap 500       # cap stores/state (smoke)
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

STORE_INDEX = "https://www.doordash.com/sitemap-store-doordash-index.xml"
_LOC = re.compile(r"<loc>([^<]+)</loc>")
_STORE = re.compile(r"/store/(?:(?P<slug>[\w%.'-]*?)-)?(?P<id>\d{4,9})/?$")
_STATE = re.compile(r"sitemap-doordash-([a-z]{2})-stores", re.I)
STORE_FIELDS = ["store_id", "name", "city", "state", "url", "type", "source"]


def _get(url, tries=4, log=print):
    """$0 sitemap fetch — curl_cffi Safari-17 through the ISP pool (rotating exit), accepting a 200 whose
    body actually looks like a sitemap (`<loc>`). No Bright Data. '' after `tries` (skip, never spend)."""
    import resi
    try:
        from curl_cffi import requests as cr
    except Exception as e:
        log("  [dd-sitemap] curl_cffi unavailable: %s" % str(e)[:60])
        return ""
    last = ""
    for a in range(tries):
        u = resi.isp_url() if resi.isp_enabled() else None
        px = {"http": u, "https": u} if u else None
        try:
            r = cr.get(url, impersonate="safari17_0", proxies=px, timeout=45)
            if r.status_code == 200 and "<loc>" in r.text:
                return r.text
            last = "status %s" % r.status_code
        except Exception as e:
            last = str(e)[:70]
        time.sleep(1 + a)
    log("  [dd-sitemap] fetch failed after %d (%s): %s" % (tries, last, url[:70]))
    return ""


def _city_from_slug(slug, state):
    """The name-slug ends with the city ('la-carreta-pura-vida-portland' → city 'portland'). We can't split
    name vs city perfectly, so keep the whole slug as the name and take the LAST hyphen token as a best-effort
    city hint — the clean city/state lives on the store page and is enriched there if needed."""
    toks = [t for t in (slug or "").split("-") if t]
    return (toks[-1] if toks else "")


def harvest(states=None, cap_per_state=None, log=print):
    """Read the store index → per-state sub-sitemaps → {store_id, name, city, state, url} rows. Lands
    doordash_stores (accumulate). Returns the rows harvested this run."""
    idx = _get(STORE_INDEX, log=log)
    subs = _LOC.findall(idx)
    if states:
        want = {s.strip().lower() for s in states}
        subs = [s for s in subs if (_STATE.search(s) and _STATE.search(s).group(1).lower() in want)]
    log("[dd-sitemap] %d state sub-sitemaps%s" % (len(subs), (" (filtered)" if states else "")))
    rows, seen = [], set()
    for si, sub in enumerate(subs):
        m = _STATE.search(sub)
        state = m.group(1).upper() if m else ""
        body = _get(sub, log=log)
        if not body:
            continue
        n = 0
        for url in _LOC.findall(body):
            sm = _STORE.search(url)
            if not sm:
                continue
            sid = sm.group("id")
            if sid in seen:
                continue
            seen.add(sid)
            slug = sm.group("slug") or ""
            rows.append(dict(store_id=sid, name=slug.replace("-", " ").strip()[:90],
                             city=_city_from_slug(slug, state), state=state, url=url,
                             type="doordash-store", source="doordash-sitemap"))
            n += 1
            if cap_per_state and n >= cap_per_state:
                break
        log("  %s: %d stores (%d/%d sub-sitemaps)" % (state or "??", n, si + 1, len(subs)))
    if rows:
        warehouse.write_accumulate("doordash_stores", rows, key=lambda r: r.get("store_id"),
                                   fields=STORE_FIELDS)
        log("[dd-sitemap] landed %d stores -> doordash_stores" % len(rows))
    else:
        log("[dd-sitemap] no stores harvested (fetch blocked?) — nothing landed")
    return rows


def run(log=print):
    """Registry entrypoint — full national harvest."""
    return harvest(log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Harvest the DoorDash national store universe from its sitemaps ($0).")
    ap.add_argument("--states", help="comma-separated 2-letter states (default: all)")
    ap.add_argument("--cap", type=int, default=None, help="cap stores per state (smoke test)")
    a = ap.parse_args(argv)
    sts = [s for s in (a.states or "").split(",") if s.strip()] or None
    rows = harvest(states=sts, cap_per_state=a.cap)
    print("harvested %d stores" % len(rows))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
