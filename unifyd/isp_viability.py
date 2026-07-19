#!/usr/bin/env python3
"""isp_viability.py — classify which chain sources can move onto the flat-rate ISP pool.

The ISP pool is validated for UberEats (15/15). This probes each OTHER chain's real surface through rotating
pool IPs and classifies PASS (real content — ISP-viable, move it off metered/BD) vs BLOCK (reputation/challenge
— needs a browser-warm confirm or premium residential). Total Wine is the known-BLOCK control.

  python isp_viability.py            # curl-through-ISP first pass across all sources

NOTE: a curl block isn't final for cookie-gated sources (a warmed browser through the same IP may pass, as with
UberEats). BLOCK here = "needs the deeper browser-warm-through-ISP test" (which already returned BLOCK for Total
Wine). PASS = content came back with no cookie at all → unambiguously ISP-viable."""
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resi

SOURCES = [
    ("kroger",    "https://www.kroger.com/atlas/v1/search/v1/products?filter.query=vodka&filter.locationId=01400943"),
    ("7now",      "https://www.7now.com/"),
    ("walmart",   "https://www.walmart.com/search?q=vodka"),
    ("abc-fws",   "https://www.abcfws.com/"),
    ("target",    "https://www.target.com/"),
    ("specs",     "https://specsonline.com/"),
    ("binnys",    "https://www.binnys.com/"),
    ("publix",    "https://www.publix.com/"),
    ("totalwine", "https://www.totalwine.com/wine/red-wine/cabernet-sauvignon/caymus-cabernet/p/223968750"),
]
BLOCK_MARKERS = ("access denied", "px-captcha", "perimeterx", "_incapsula_", "incapsula",
                 "request unsuccessful", "reference #", "robot or human", "pardon the interruption",
                 "are you a human", "bot detection", "distil", "cf-chl", "just a moment")


def classify(status, body):
    low = (body or "")[:6000].lower()
    marker = next((m for m in BLOCK_MARKERS if m in low), None)
    if status == 200 and len(body or "") > 12000 and not marker:
        return "PASS", "real content (%dKB)" % (len(body) // 1024)
    if status in (401, 403, 407, 429, 503) or marker:
        return "BLOCK", (("marker:'%s'" % marker) if marker else "status %s" % status)
    if status == 200:
        return "THIN", "200 but small (%dB) — maybe challenge/JSON" % len(body or "")
    return "OTHER", "status %s" % status


def probe(name, url):
    from curl_cffi import requests as cr
    best = None
    for i in range(2):
        px = resi.isp_url("%s%d" % (name, i))
        try:
            r = cr.Session(impersonate="chrome", timeout=30, proxies={"http": px, "https": px}).get(url)
            verdict, why = classify(r.status_code, r.text)
        except Exception as e:
            verdict, why = "ERR", str(e)[:40]
        cand = {"name": name, "verdict": verdict, "why": why, "status": getattr(locals().get("r", None), "status_code", "-")}
        if verdict == "PASS":
            return cand
        best = cand if best is None else best
    return best


def main():
    if not resi.isp_enabled():
        print("ISP pool empty — set ISP_PROXIES / isp_proxies.txt first."); return
    print("Probing %d chain sources through the ISP pool (%d IPs)…\n" % (len(SOURCES), len(resi.isp_pool())))
    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(probe, n, u): n for n, u in SOURCES}
        for f in as_completed(futs):
            r = f.result(); rows.append(r)
    order = {"PASS": 0, "THIN": 1, "BLOCK": 2, "ERR": 3, "OTHER": 4}
    for r in sorted(rows, key=lambda x: (order.get(x["verdict"], 9), x["name"])):
        print("  %-6s %-11s %s" % (r["verdict"], r["name"], r["why"]))
    ok = [r["name"] for r in rows if r["verdict"] == "PASS"]
    thin = [r["name"] for r in rows if r["verdict"] == "THIN"]
    blk = [r["name"] for r in rows if r["verdict"] in ("BLOCK", "ERR", "OTHER")]
    print("\nISP-VIABLE (move off metered/BD):", ok or "none")
    print("NEEDS browser-warm confirm:", thin or "none")
    print("BLOCKED at curl (warm-test or premium):", blk or "none")


if __name__ == "__main__":
    import kroger_api
    kroger_api._load_creds()
    main()
