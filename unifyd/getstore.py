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
import threading
import uuid as _uuid

API = "https://www.ubereats.com/_p/api/getStoreV1"
_TL = threading.local()                                    # one primed curl_cffi session PER worker thread
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


def _session(site):
    """A curl_cffi session primed once for THIS worker thread — the homepage GET (cookie prime) happens once,
    then every getStoreV1 POST reuses it. This is what makes a high-concurrency sweep near ~1h instead of
    paying a homepage round-trip per store."""
    s = getattr(_TL, "s", None)
    if s is not None:
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
        px = pool[(threading.get_ident() // 8) % len(pool)]     # sticky per thread, spread across the pool
    else:
        px = resi._session_url("ag%d" % (threading.get_ident() % 400)) if resi.enabled() else None
    s = cr.Session(impersonate="chrome", proxies={"http": px, "https": px} if px else None, timeout=30)
    try:
        s.get(_base(site))
    except Exception:
        pass
    _TL.s = s
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
    try:
        return ((s.post(api, json=body, headers=H).json()) or {}).get("data")
    except Exception:
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
