#!/usr/bin/env python3
"""isp_check.py — go/no-go test for the flat-rate ISP proxy pool.

The whole cost-effective plan hinges on one unknown: does a cheap ISP/static-residential IP pass UberEats'
PerimeterX? This fetches a real /store/ page + the exit geo through EVERY IP in ISP_PROXIES and reports which
pass (HTTP 200 + parseable JSON-LD), which are challenged/dead, and where each exits. Run it the moment the
IPRoyal ISP list is in ISP_PROXIES:  python isp_check.py

VERDICT: if most IPs return real content, flip geofill + the daily engine onto the pool (flat-rate, unlimited
GB). If they're uniformly challenged, ISP reputation isn't enough for this gate and we stay on rotating
residential for the geo-feeds + browser for catalogs. Dead/challenged IPs are printed so you can ask IPRoyal
to swap them."""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resi
import ue_geofill

STORE = "https://www.ubereats.com/store/los-taquitos-de-puebla-ii/04ky9hfzRImUMfHeyrHqLw"


def _exit_geo(px):
    try:
        from curl_cffi import requests as cr
        import json
        r = cr.Session(impersonate="chrome", timeout=25, proxies={"http": px, "https": px}).get(
            "https://ipapi.co/json/", headers={"User-Agent": "curl/8"})
        d = json.loads(r.text)
        return "%s · %s,%s · %s" % (d.get("ip", "?"), d.get("region_code", "?"), d.get("country_code", "?"),
                                    d.get("org", "")[:28])
    except Exception as e:
        return "geo? " + str(e)[:30]


def check_one(px, idx):
    t0 = time.time()
    try:
        st, doc = ue_geofill._fetch(STORE, px)
    except Exception as e:
        return {"idx": idx, "ok": False, "status": "ERR", "geo": _exit_geo(px), "note": str(e)[:40]}
    real = bool(doc and ue_geofill.parse(doc))
    return {"idx": idx, "ok": real, "status": st, "geo": _exit_geo(px),
            "note": "real content" if real else ("challenge/empty (%dB)" % len(doc)), "ms": int((time.time() - t0) * 1000)}


def main():
    pool = resi.isp_pool()
    if not pool:
        print("ISP_PROXIES is empty. Paste the IPRoyal ISP list into ISP_PROXIES (one endpoint per line) first.")
        print("  accepted formats: http://user:pass@host:port | user:pass@host:port | host:port:user:pass")
        return
    print("Testing %d ISP IPs against UberEats PerimeterX…\n" % len(pool))
    results = []
    with ThreadPoolExecutor(max_workers=min(12, len(pool))) as ex:
        futs = {ex.submit(check_one, px, i): i for i, px in enumerate(pool)}
        for f in as_completed(futs):
            r = f.result(); results.append(r)
            mark = "PASS" if r["ok"] else "FAIL"
            print("  [%s] #%-3d %-5s  %-40s  %s" % (mark, r["idx"], r["status"], r["geo"], r["note"]))
    ok = sum(1 for r in results if r["ok"])
    print("\n=== %d/%d IPs pass (real content) ===" % (ok, len(pool)))
    if ok >= 0.7 * len(pool):
        print("VERDICT: VIABLE — flip geofill + daily engine onto the ISP pool (flat-rate, unlimited GB).")
    elif ok:
        print("VERDICT: PARTIAL — usable but ask IPRoyal to swap the failing IPs; the passing ones work.")
    else:
        print("VERDICT: NOT VIABLE for this gate — keep rotating residential for feeds + browser for catalogs.")
    bad = [r["idx"] for r in sorted(results, key=lambda x: x["idx"]) if not r["ok"]]
    if bad:
        print("failing IP indexes (ask IPRoyal to replace):", bad)


if __name__ == "__main__":
    import kroger_api
    kroger_api._load_creds()
    main()
