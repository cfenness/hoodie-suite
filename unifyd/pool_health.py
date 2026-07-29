#!/usr/bin/env python3
"""pool_health.py — ask every exit in the ISP pool what it actually is.

WHY THIS EXISTS. The pool is assembled from two places (the ISP_PROXIES env var AND isp_proxies.txt),
merged and de-duped, so what the code uses is not necessarily what anyone believes they are paying for.
A stale entry does not announce itself: it either fails quietly and looks like a block, or it works and
routes a request for a US store through an exit in another country — which is indistinguishable from
"the target got harder" unless someone checks.

What it reports per entry: reachable, the exit IP as the wider internet sees it, and the country. A
geography mismatch is the finding — a US food-delivery API answering a request for a US store from a
Ukrainian exit will challenge it, and no amount of rate tuning or session rotation will change that.

    python3 pool_health.py
"""
import concurrent.futures as cf
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Plain, unauthenticated echo endpoints — deliberately not the target, so a challenge from the target
# cannot be confused with a broken proxy.
ECHO = "http://ip-api.com/json/?fields=query,country,countryCode,isp"


def _check(entry, timeout=15):
    host = entry.split("@")[-1].split(":")[0]
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": entry, "https": entry}))
        with op.open(ECHO, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return {"entry": host, "ok": True, "exit": d.get("query"), "cc": d.get("countryCode"),
                "country": d.get("country"), "isp": (d.get("isp") or "")[:32]}
    except Exception as e:
        return {"entry": host, "ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:60])}


def run(log=print):
    import resi
    pool = resi.isp_pool()
    log("pool entries: %d" % len(pool))
    if not pool:
        log("  (none — FETCH_POLICY=free, or neither ISP_PROXIES nor isp_proxies.txt is set)")
        return 1
    with cf.ThreadPoolExecutor(max_workers=min(20, len(pool))) as ex:
        res = list(ex.map(_check, pool))
    by_cc = {}
    dead = 0
    for r in sorted(res, key=lambda x: (not x["ok"], x.get("cc") or "", x["entry"])):
        if r["ok"]:
            by_cc[r["cc"]] = by_cc.get(r["cc"], 0) + 1
            log("  %-16s -> %-15s %-3s %s" % (r["entry"], r["exit"], r["cc"], r["isp"]))
        else:
            dead += 1
            log("  %-16s -> DEAD  %s" % (r["entry"], r["error"]))
    log("")
    log("reachable: %d/%d | dead: %d" % (len(res) - dead, len(res), dead))
    log("countries: %s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(by_cc.items(), key=lambda x: -x[1])))
    non_us = sum(v for k, v in by_cc.items() if k not in ("US",))
    if non_us:
        log("!! %d exit(s) are NOT US. A US target answering a US-store request from a foreign exit will"
            % non_us)
        log("   challenge it — that is a geography problem, not a rate or fingerprint problem.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
