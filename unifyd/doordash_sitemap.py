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
import json
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


# US states + DC. Canadian provinces/territories ride the same index (YT legitimately has ~60 stores),
# so the plausibility floor below must not be applied to them or it cries wolf every run.
_US = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
       "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM",
       "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
       "WV", "WI", "WY"}
# The thinnest real US states measured live are VT 747 / WY 938 / AK 957. 300 sits well below the
# genuine floor, so anything under it is a broken feed, not a small state.
THIN_FLOOR = int(os.environ.get("DD_STATE_FLOOR", "300"))
REGRESSION_PCT = 0.25          # harvested < 25% of what we already hold for that state = collapse


def _stored_state_counts():
    """{STATE: n} already in doordash_stores — the yardstick for "did this state just collapse"."""
    try:
        rows = warehouse.query(
            "doordash_stores",
            "SELECT upper(CAST(state AS VARCHAR)) st, count(*) n FROM t GROUP BY 1")
        return {r["st"]: int(r["n"] or 0) for r in rows if r["st"]}
    except Exception:                                          # noqa: BLE001 — no baseline is not fatal
        return {}


def harvest(states=None, cap_per_state=None, log=print):
    """Read the store index → per-state sub-sitemaps → {store_id, name, city, state, url} rows. Lands
    doordash_stores (accumulate). Returns the rows harvested this run.

    SELF-REPORTS a per-state plausibility verdict. It previously did not, and the cost was measured:
    DoorDash's own `sitemap-doordash-tx-stores.xml` serves a stub containing ONE store (California's
    has 103,811). We harvested that 1, landed it, and reported success — so Texas sat at a single
    outlet while every other state carried tens of thousands, and nothing anywhere said so. A locator
    search in Houston returned nothing from DoorDash and looked like a coverage fact rather than a
    broken feed.

    A state that fetches but yields an implausible count is now a `degraded` run with the state named.
    """
    idx = _get(STORE_INDEX, log=log)
    subs = _LOC.findall(idx)
    if states:
        want = {s.strip().lower() for s in states}
        subs = [s for s in subs if (_STATE.search(s) and _STATE.search(s).group(1).lower() in want)]
    log("[dd-sitemap] %d state sub-sitemaps%s" % (len(subs), (" (filtered)" if states else "")))
    rows, seen = [], set()
    per_state, fetch_failed = {}, []
    for si, sub in enumerate(subs):
        m = _STATE.search(sub)
        state = m.group(1).upper() if m else ""
        body = _get(sub, log=log)
        if not body:
            # Was `continue` alone: a state whose sub-sitemap failed every retry vanished from the run
            # with no counter, no status change and no summary line. Record it so a partial harvest
            # can never again be indistinguishable from a complete one.
            fetch_failed.append(state or sub.split("/")[-1])
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
        per_state[state] = per_state.get(state, 0) + n
        log("  %s: %d stores (%d/%d sub-sitemaps)" % (state or "??", n, si + 1, len(subs)))
    if rows:
        warehouse.write_accumulate("doordash_stores", rows, key=lambda r: r.get("store_id"),
                                   fields=STORE_FIELDS)
        log("[dd-sitemap] landed %d stores -> doordash_stores" % len(rows))
    else:
        log("[dd-sitemap] no stores harvested (fetch blocked?) — nothing landed")

    # ── plausibility, stated out loud ───────────────────────────────────────────────────────────
    prev = _stored_state_counts()
    warnings = []
    if fetch_failed:
        warnings.append("sub-sitemap fetch failed for %d region(s): %s"
                        % (len(fetch_failed), ", ".join(sorted(set(fetch_failed))[:12])))
    thin = sorted(st for st, n in per_state.items()
                  if st in _US and n < THIN_FLOOR and not (cap_per_state and n >= cap_per_state))
    if thin:
        warnings.append("US state(s) below the %d-store floor — the feed is broken, not the market: %s"
                        % (THIN_FLOOR, ", ".join("%s=%d" % (s, per_state[s]) for s in thin)))
    collapsed = sorted(st for st, n in per_state.items()
                       if prev.get(st, 0) >= THIN_FLOOR and n < REGRESSION_PCT * prev[st])
    if collapsed:
        warnings.append("state(s) collapsed vs what we already hold: "
                        + ", ".join("%s %d→%d" % (s, prev[s], per_state[s]) for s in collapsed))

    # The scraper knows its own denominator; say it, so coverage.assess can't fall back to a
    # high-water mark that a collapsed run would quietly reset.
    expected = sum(max(per_state.get(st, 0), prev.get(st, 0)) for st in set(per_state) | set(prev))
    result = {"status": "degraded" if warnings else "success",
              "stores_total": expected, "stores_done": len(rows),
              "states": len(per_state), "warnings": warnings}
    for w in warnings:
        log("  [dd-sitemap] DEGRADED — %s" % w)
    sys.stderr.write("HOODIE_RESULT %s\n" % json.dumps(result))
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
