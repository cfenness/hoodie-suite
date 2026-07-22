#!/usr/bin/env python3
"""target_redsky_probe.py — GROUNDED test: can we read Target's RedSky API from a datacenter IP, for FREE?

The question the code never actually answered: RedSky's auth is the PUBLIC web key (already in
target_scraper.py: SEARCH_KEY). The only "tested 403" was a warmed cookie transferred to curl — NOT
curl_cffi Chrome-TLS direct, and NOT from a cloud datacenter IP with no proxy. So before anyone claims
Target "needs" a residential proxy or the Mac, run THIS from a free cloud runner (GitHub Actions ubuntu:
Azure datacenter IP, no proxy) and read the result.

No proxy. No paid anything. Just curl_cffi impersonating Chrome (TLS + HTTP2 fingerprint) hitting the
same endpoints target.com's own frontend calls. Prints, per endpoint: HTTP status + whether real JSON
data came back. Exit 0 if ANY endpoint returned data (=> free cloud path works), 1 if all blocked.
"""
import json
import sys
import urllib.parse

REDSKY = "https://redsky.target.com/redsky_aggregations/v1/web"
SEARCH_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"     # Target's PUBLIC web search key (from target_scraper.py)

CALLS = [
    ("nearby_stores_v1", {"key": SEARCH_KEY, "limit": 20, "within": 100, "place": "10001", "channel": "WEB"},
     lambda d: (((d.get("data") or {}).get("nearby_stores") or {}).get("stores")) or []),
    ("plp_search_v2", {"key": SEARCH_KEY, "channel": "WEB", "count": 28, "default_purchasability_filter": "true",
                       "keyword": "vodka", "offset": 0, "page": "/s/vodka", "platform": "desktop",
                       "pricing_store_id": "1density", "store_ids": "1", "visitor_id": "0193", "zip": "10001"},
     lambda d: (((d.get("data") or {}).get("search") or {}).get("products")) or []),
]
HEADERS = {"accept": "application/json", "origin": "https://www.target.com",
           "referer": "https://www.target.com/", "accept-language": "en-US,en;q=0.9"}


def _probe(impersonate):
    from curl_cffi import requests as cr
    s = cr.Session(impersonate=impersonate, timeout=30)      # NO proxies= : direct from this runner's IP
    ok_any = False
    for name, q, extract in CALLS:
        url = "%s/%s?%s" % (REDSKY, name, urllib.parse.urlencode(q))
        try:
            r = s.get(url, headers=HEADERS)
            body = r.text or ""
            got = []
            if r.status_code == 200 and body[:1] in "{[":
                try:
                    got = extract(json.loads(body))
                except Exception:
                    got = []
            ok = bool(got)
            ok_any = ok_any or ok
            print("  [%s] %-26s HTTP %s  bytes=%-7d  data=%s%s" % (
                impersonate, name, r.status_code, len(body), ("%d rows" % len(got)) if ok else "NONE",
                "" if r.status_code == 200 else "  (blocked)"))
            if not ok and body:
                snippet = body.replace("\n", " ")[:120]
                print("        ↳ %s" % snippet)
        except Exception as e:
            print("  [%s] %-26s ERROR %s" % (impersonate, name, str(e)[:100]))
    return ok_any


def main():
    print("Target RedSky FREE-from-cloud probe — no proxy, public key, curl_cffi Chrome-TLS")
    print("runner IP is a datacenter IP; if this returns data, Target needs NO residential proxy.\n")
    any_ok = False
    for imp in ("chrome124", "chrome120", "chrome"):
        try:
            any_ok = _probe(imp) or any_ok
        except Exception as e:
            print("  [%s] probe failed to start: %s" % (imp, str(e)[:120]))
        if any_ok:
            break
    print("\nVERDICT: %s" % ("FREE CLOUD PATH WORKS — RedSky returned data from a datacenter IP, no proxy."
                             if any_ok else
                             "blocked from THIS datacenter IP — grounded evidence; try a browser tier / different IP next."))
    return 0 if any_ok else 1


if __name__ == "__main__":
    sys.exit(main())
