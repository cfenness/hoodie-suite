#!/usr/bin/env python3
"""getstore.py — call UberEats/Postmates getStoreV1 DIRECTLY (the stable JSON API), COLD, for exact store geo.

The store page HTML is heavy and its schema.org is flaky; getStoreV1 is a small JSON POST that returns the
store's `location` block — exact latitude/longitude PLUS a full street address, city, region, postalCode. And
it's cold-callable: like ue_crawl.geocode, a fresh curl_cffi session + `x-csrf-token: x` clears it, no warmed
browser. The store's own address is intrinsic, so it comes back regardless of the delivery target.

The sitemap gives the URL id ('PXfwbCKPUgKVU4_0jnXzcg'); getStoreV1 wants the dashed API uuid
('3d77f06c-…') — and the URL id IS base64url(uuid bytes), so url_id_to_uuid() converts with no lookup
(verified against a live capture). That's what lets us call getStoreV1 for all ~495k sitemap stores directly.
"""
import base64
import os
import threading
import uuid as _uuid

API = "https://www.ubereats.com/_p/api/getStoreV1"
_TL = threading.local()                                    # one primed curl_cffi session PER worker thread
_RR, _RR_LOCK = [0], threading.Lock()                      # even round-robin over the ISP pool
# Requests per primed session before re-priming. Set below the ~50 where collapse was observed.
SESSION_BUDGET = int(os.environ.get("UE_SESSION_BUDGET", "40"))
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
_H = {"accept": "*/*", "accept-language": "en-US,en;q=0.9", "content-type": "application/json",
      "origin": "https://www.ubereats.com", "x-csrf-token": "x", "x-uber-client-gitref": "x", "user-agent": _UA}


def url_id_to_uuid(url_id):
    """Uber store URL id (base64url of the 16 uuid bytes) -> dashed API uuid. None if it isn't 16 bytes."""
    try:
        b = base64.urlsafe_b64decode((url_id or "") + "=" * (-len(url_id or "") % 4))
        return str(_uuid.UUID(bytes=b)) if len(b) == 16 else None
    except Exception:
        return None


def _base(site):
    return "https://postmates.com" if site == "postmates" else "https://www.ubereats.com"


# ── THE BROWSER RUNG ─────────────────────────────────────────────────────────────────────────────
# What the ladder escalates INTO when the cold path closes. browser_warm already solves the hard part,
# and its own docstring names today's adversary: PerimeterX binds _px3 to the TLS FINGERPRINT, so a
# requests/curl_cffi session carrying a warmed cookie gets re-challenged — which is exactly the failure
# we spent today watching. Running the fetch INSIDE the page means the request inherits the real
# fingerprint and the auto-refreshing token together, so there is nothing to re-challenge.
#
# ONE browser per process, not per thread. A Chromium instance is RAM-heavy and this rung is 10-50x
# slower than a raw fetch; the ladder knows that, which is why it only climbs here under duress and
# decays back down afterwards. In-page fetches serialize behind the lock, and that is correct — the
# whole point of this rung is to be quiet, not parallel.
_BR = {"w": None, "site": None}
_BR_LOCK = threading.Lock()


def _browser(site):
    """The warmed browser for this site, launched on first use and reused for the life of the process.
    Pinned to a residential exit: the proven recipe for this target is a real browser on a residential
    IP — a real browser on a datacenter IP is still a datacenter IP."""
    with _BR_LOCK:
        if _BR["w"] is not None and _BR["site"] == site:
            return _BR["w"]
        import browser_warm
        proxy = None
        try:
            import resi
            pool = resi.isp_pool()
            if pool:
                with _RR_LOCK:
                    _RR[0] += 1
                    proxy = pool[_RR[0] % len(pool)]
        except Exception:
            proxy = None
        dom = _base(site).replace("https://", "").replace("http://", "")
        # channel="chrome" — the Fly image ships the REAL Google Chrome at /usr/bin/google-chrome and no
        # bundled Chromium, so a default launch would look for a browser that is not there. Using the
        # real Chrome is also the better fingerprint: genuine build, genuine GPU strings, which is the
        # entire reason this rung exists.
        _BR["w"] = browser_warm.Warmer(dom, proxy=proxy, patchright=True,
                                       channel=os.environ.get("BROWSER_CHANNEL", "chrome"))
        _BR["site"] = site
        return _BR["w"]


def _fetch_browser(site, api, body, headers):
    """One getStoreV1 POST through the warmed page. Returns (status, data-or-None)."""
    w = _browser(site)
    with _BR_LOCK:
        status, obj = w.post_json(api, body, headers=headers)
    return status, ((obj or {}).get("data") if isinstance(obj, dict) else None)


def _exit_ip():
    """The exit this thread's session is pinned to, so an outcome can be attributed to the identity that
    produced it rather than to the pool as a whole."""
    return getattr(_TL, "exit", None)


def _method():
    """Which rung of the ladder this request goes out on. Today it is the flat-rate ISP pool or bare
    egress; naming it now is what makes per-method success rates — and therefore an escalation router —
    possible later without re-instrumenting the fetch path."""
    try:
        import resi
        return "isp" if resi.isp_pool() else "direct"
    except Exception:
        return "direct"


def _session(site):
    """A curl_cffi session primed once for THIS worker thread — the homepage GET (cookie prime) happens once,
    then every getStoreV1 POST reuses it. This is what makes a high-concurrency sweep near ~1h instead of
    paying a homepage round-trip per store."""
    # ROTATE THE SESSION ON A REQUEST BUDGET. Measured across three runs, the endpoint stops answering
    # after roughly the same NUMBER of requests rather than after a period of time:
    #
    #   unpaced, 120 workers   healthy ~60s, ~5,578 requests   (~46 per session)
    #   paced at 40 req/s      healthy ~60s, ~7,000 requests   (~58 per session)
    #
    # Both collapsed at a similar request COUNT, and halving the rate bought only a longer wait for the
    # same wall — which is what a QUOTA looks like, not a rate limit. It also explains why 20 exit IPs
    # never helped (the quota is not per-IP) and why every rate model we fitted was wrong. The session
    # was primed once per thread and reused forever, so its cookie carried the whole budget.
    #
    # Re-priming costs one homepage GET and resets that budget. Set SESSION_BUDGET=0 to disable and get
    # the old behaviour back if this turns out to be the wrong read.
    n = getattr(_TL, "n", 0)
    s = getattr(_TL, "s", None)
    _pol = None
    try:
        import sessions
        _pol = sessions.policy_for(site)
    except Exception:
        _pol = None
    _spent = _pol.spent(n) if _pol else (SESSION_BUDGET and n >= SESSION_BUDGET)
    if s is not None and _spent:
        try:
            s.close()
        except Exception:
            pass
        s, _TL.s = None, None             # fall through and prime a fresh one
        _TL.n = 0
        if _pol:
            _pol.note_retire()
    if s is not None:
        _TL.n = n + 1
        return s
    from curl_cffi import requests as cr
    import resi
    # EXIT IP per thread. The FLAT-RATE ISP pool first: fixed price per IP, unlimited bandwidth, no
    # variable cost. This is what makes a full daily sweep possible — the BFF throttles PER IP, and
    # measured on a single exit it stops answering above ~32 concurrent workers (returning empty fast,
    # with no 429). Spreading threads across the pool multiplies the ceiling without buying bandwidth.
    #
    # _session_url() (the metered per-GB rotating tier) is deliberately NOT used: paygo_allowed() is
    # False by default, so it returns None anyway, and every thread then shares the one Fly egress —
    # which is precisely the throttle we measured. Flat-rate IPs, never per-GB.
    pool = []
    try:
        pool = resi.isp_pool()
    except Exception:
        pool = []
    if pool:
        # ROUND-ROBIN by an explicit counter, not by thread ident. Thread ids are arbitrary values, so
        # `ident % len(pool)` distributes unevenly — several workers can land on one IP while others go
        # unused, which concentrates load exactly where the limit is and looks like a smaller pool.
        with _RR_LOCK:
            _RR[0] += 1
            px = pool[_RR[0] % len(pool)]
        try:
            _TL.exit = px.split("@")[-1].split(":")[0]
        except Exception:
            _TL.exit = None
    else:
        px = resi._session_url("ag%d" % (threading.get_ident() % 400)) if resi.enabled() else None
    s = cr.Session(impersonate="chrome", proxies={"http": px, "https": px} if px else None, timeout=30)
    try:
        s.get(_base(site))
    except Exception:
        pass
    _TL.s = s
    _TL.n = 1
    try:
        import sessions
        sessions.policy_for(site).note_prime()
    except Exception:
        pass
    return s


def fetch_store(store_uuid, session="gs", site="ubereats", target=None):
    """POST getStoreV1 for one store (dashed uuid), COLD, reusing the thread's primed session. Returns the
    `data` dict or None. `target` (lat,lng) sets the delivery-target headers if given — not required for the
    store's own address."""
    s = _session(site)
    H = dict(_H)
    api = API if site == "ubereats" else API.replace("www.ubereats.com", "postmates.com")
    if target:
        H["x-uber-target-location-latitude"] = str(target[0]); H["x-uber-target-location-longitude"] = str(target[1])
        H["x-uber-device-location-latitude"] = str(target[0]); H["x-uber-device-location-longitude"] = str(target[1])
    body = {"storeUuid": store_uuid, "diningMode": "DELIVERY", "time": {"asap": True}, "cbType": "EATER_ENDORSED"}
    # PACE BEFORE THE REQUEST, not after. The limit we hit is an aggregate REQUEST RATE (measured:
    # ~59 req/s collapsed the fleet even at 6 concurrent per IP across 20 IPs), so the control point has
    # to be requests-per-second, not worker count — the proxy we have now guessed wrong three times.
    _p = None
    try:
        import pace
        _p = pace.get()
        if _p:
            _p.acquire()
    except Exception:
        _p = None                          # pacing must never be the reason a scrape cannot run
    try:
        # TIMEOUT IS NOT OPTIONAL. Without one a stalled socket parks this worker permanently: with 7
        # workers, seven silent hangs are a dead shard that still looks alive. The enrich path already
        # bounded its POST; this one never did.
        rung = None
        try:
            import ladder
            rung = ladder.current(site)
        except Exception:
            rung = None
        if rung == "browser":
            # The escalated path. Same body, same headers, issued from inside a trusted page.
            st, data = _fetch_browser(site, api, body, H)
            r = type("R", (), {"status_code": st, "text": "", "content": b"1"})()
        else:
            r = s.post(api, json=body, headers=H, timeout=30)
            data = ((r.json() if r.content else None) or {}).get("data")
        # CLASSIFY, then feed the controller only REAL pushback. Reporting every no-data response as a
        # throttle is what had the pacer backing off on permanently-closed stores — a condition no
        # amount of slowing down can improve, and the reason a healthy run walked itself to a standstill.
        try:
            import blocks, ue_catalog
            cls = blocks.classify(status=getattr(r, "status_code", None),
                                  body=(r.text[:4000] if getattr(r, "text", None) else ""),
                                  has_payload=ue_catalog.has_catalog(data))
            t = blocks.get()
            if t:
                t.record(cls, method=_method(), exit=_exit_ip())
            try:
                import sessions
                sessions.policy_for(site).report(cls, getattr(_TL, "n", 0))
            except Exception:
                pass
            try:
                import ladder
                ladder.report(site, cls)      # a closed rung escalates itself from here
            except Exception:
                pass
            if _p:
                _p.report(not blocks.is_throttle(cls))
        except Exception:
            if _p:
                _p.report(bool(data))
        return data
    except Exception as e:
        try:
            import blocks
            cls = blocks.classify(exc=e)
            t = blocks.get()
            if t:
                t.record(cls, method=_method(), exit=_exit_ip())
            if _p:
                _p.report(not blocks.is_throttle(cls))
        except Exception:
            if _p:
                _p.report(False)
        return None


def location_of(data):
    """Pull the store geo + full address out of a getStoreV1 `data` dict. lat is None if absent."""
    loc = (data or {}).get("location") or {}
    lat, lng = loc.get("latitude"), loc.get("longitude")
    return {"lat": lat if isinstance(lat, (int, float)) else None,
            "lng": lng if isinstance(lng, (int, float)) else None,
            "street": loc.get("streetAddress") or "", "city": loc.get("city") or "",
            "state": (loc.get("region") or "")[:2].upper() if loc.get("region") else "",
            "zip": loc.get("postalCode") or "", "name": (data or {}).get("title") or ""}


def store_geo(url_or_uuid, session="gs", site="ubereats", target=None):
    """Convenience: accept a sitemap URL id OR a dashed uuid, fetch, return the location dict (or {})."""
    su = url_or_uuid if "-" in (url_or_uuid or "") and len(url_or_uuid) == 36 else url_id_to_uuid(url_or_uuid)
    if not su:
        return {}
    return location_of(fetch_store(su, session=session, site=site, target=target))


if __name__ == "__main__":
    import sys
    print(store_geo(sys.argv[1] if len(sys.argv) > 1 else "PXfwbCKPUgKVU4_0jnXzcg"))
