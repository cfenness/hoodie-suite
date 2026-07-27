#!/usr/bin/env python3
"""outlet_ident.py — the on-premise OUTLET identity model: extract identity signals from a source page, and
resolve the SAME physical outlet across sources (DoorDash / Toast / UberEats / Postmates / …).

Name alone never dedups a chain ("Don Julio" is a hundred bars). The reliable cross-source keys are the ones
every source stamps on its store page — which we already fetch for the menu:
  - PHONE  (10 digits)                — exact, per-outlet unique → the strongest key
  - ADDRESS(street-number + street-token + state) — strong
  - GEO    (lat/lng rounded ~11m)     — strong where precise (Toast yes; DoorDash ships a US-centroid default,
                                        so we ignore DoorDash geo)
  - NAME + city + state               — fallback block only

Resolution = union-find over the STRONG keys (phone/address/geo). Records that share any strong key collapse
to one outlet; the rest stay provisional (grouped by name+city+state) until a page fetch enriches them. So
matching sharpens automatically as menu-pull coverage grows — which is why it's cheap to get right up front.
"""
import re

# ── signal extraction from a fetched store page ──────────────────────────────────────────────────────────
_T_LAT = re.compile(r'"latitude":\s*(-?\d+\.\d+)')
_T_LNG = re.compile(r'"longitude":\s*(-?\d+\.\d+)')
_T_NAME = re.compile(r'"restaurantName":"([^"]+)"|"name":"([^"]{3,60})"')
_T_ADDR = re.compile(r'"address1":"([^"]+)"|"addressLine1":"([^"]+)"')
_T_CITY = re.compile(r'"city":"([^"]+)"')
_T_STATE = re.compile(r'"(?:addressState|state|stateCode)":"([A-Z]{2})"')
_PHONE = re.compile(r'"(?:phone|telephone|phoneNumber|displayPhone)":"?\+?1?[\s-]?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})')
_DD_STREET = re.compile(r'\\?"street\\?":\\?"([^"\\]+)')
_DD_CITY = re.compile(r'\\?"city\\?":\\?"([^"\\]+)')
_DD_STATE = re.compile(r'\\?"state\\?":\\?"([A-Z]{2})\\?"')
_DD_NAME = re.compile(r'\\?"(?:businessName|name)\\?":\\?"([^"\\]{3,60})')


_UE_STREET = re.compile(r'"streetAddress":"([^"]+)"')
_UE_CITY = re.compile(r'"addressLocality":"([^"]+)"')
_UE_STATE = re.compile(r'"addressRegion":"([A-Z]{2})"')
_UE_LAT = re.compile(r'"latitude":\s*(-?\d+\.\d+)')
_UE_LNG = re.compile(r'"longitude":\s*(-?\d+\.\d+)')
_UE_NAME = re.compile(r'"@type":"Restaurant"[^}]*?"name":"([^"]{3,60})"|"title":"([^"]{3,60})"')

# The store's getStoreV1 `location` object — the RELIABLE source (schema.org JSON-LD is often absent). It packs
# exact geo + a full street address in one block: {"streetAddress":…,"city":…,"region":"FL","postalCode":…,
# "latitude":…,"longitude":…}. We scope to a window after the `"location":{` anchor so the STORE's coords win
# over any delivery-target/user-location lat/lng elsewhere on the page.
_UE_LOC_ANCHOR = re.compile(r'"location"\s*:\s*\{')


def _ue_location(html):
    m = _UE_LOC_ANCHOR.search(html)
    if not m:
        return {}
    win = html[m.end():m.end() + 1400]                     # the location object is small; bound the scan

    def g(pat):
        x = re.search(pat, win)
        return x.group(1).strip() if x else ""
    lat, lng = g(r'"latitude":\s*(-?\d+\.\d+)'), g(r'"longitude":\s*(-?\d+\.\d+)')
    return {"street": g(r'"streetAddress":"([^"]+)"'), "city": g(r'"city":"([^"]+)"'),
            "state": g(r'"region":"([A-Z]{2})"'), "zip": g(r'"postalCode":"([^"]+)"'),
            "lat": float(lat) if lat else None, "lng": float(lng) if lng else None}


def _first(*groups):
    for g in groups:
        if g:
            return g
    return ""


def extract_ubereats(html):
    """UberEats/Postmates store pages embed the getStoreV1 `location` object (exact geo + full street address +
    city/state/zip) — the primary source; schema.org Restaurant JSON-LD is the fallback. Fetchable $0."""
    loc = _ue_location(html)
    st = _UE_STREET.search(html)
    ci = _UE_CITY.search(html)
    stt = _UE_STATE.search(html)
    la = _UE_LAT.search(html)
    lo = _UE_LNG.search(html)
    ph = _PHONE.search(html)
    nm = _UE_NAME.search(html)
    return {"name": _first(*(nm.groups() if nm else ())).strip(),
            "lat": loc.get("lat") if loc.get("lat") is not None else (float(la.group(1)) if la else None),
            "lng": loc.get("lng") if loc.get("lng") is not None else (float(lo.group(1)) if lo else None),
            "street": loc.get("street") or (st.group(1) if st else "").strip(),
            "city": loc.get("city") or (ci.group(1) if ci else "").strip(),
            "state": loc.get("state") or (stt.group(1) if stt else "").strip(),
            "zip": loc.get("zip", ""),
            "phone": ("".join(ph.groups()) if ph else "")}


def extract_toast(html):
    lat = _T_LAT.search(html)
    lng = _T_LNG.search(html)
    nm = _T_NAME.search(html)
    ad = _T_ADDR.search(html)
    ph = _PHONE.search(html)
    return {"name": _first(*(nm.groups() if nm else ())).strip(),
            "lat": float(lat.group(1)) if lat else None,
            "lng": float(lng.group(1)) if lng else None,
            "street": _first(*(ad.groups() if ad else ())).strip(),
            "city": (_T_CITY.search(html).group(1) if _T_CITY.search(html) else "").strip(),
            "state": (_T_STATE.search(html).group(1) if _T_STATE.search(html) else "").strip(),
            "phone": ("".join(ph.groups()) if ph else "")}


def extract_doordash(html):
    # DoorDash's RSC address block is the reliable identity: street + city + state. The RSC has no usable
    # phone (only a "Phone Number" label) and no per-store geo (US-centroid default), and the store NAME in
    # the RSC is unreliable (telemetry keys match first) — the caller passes the real name it already parsed.
    st = _DD_STREET.search(html)
    ci = _DD_CITY.search(html)
    stt = _DD_STATE.search(html)
    return {"name": "", "lat": None, "lng": None,
            "street": (st.group(1) if st else "").strip(),
            "city": (ci.group(1) if ci else "").strip(),
            "state": (stt.group(1) if stt else "").strip(),
            "phone": ""}


# ── normalized match keys ────────────────────────────────────────────────────────────────────────────────
def phone_key(phone):
    d = re.sub(r"\D", "", phone or "")
    d = d[-10:] if len(d) >= 10 else ""
    return ("ph:" + d) if len(d) == 10 and d[0] not in "01" else ""


def addr_key(street, city, state):
    s = (street or "").lower()
    num = re.match(r"\s*(\d+)", s)
    toks = re.findall(r"[a-z]+", re.sub(r"\b(n|s|e|w|north|south|east|west|st|street|ave|avenue|rd|road|blvd|"
                                        r"boulevard|dr|drive|ln|lane|ste|suite|unit|hwy|pkwy|parkway)\b", " ", s))
    core = (toks[0] if toks else "")
    if not (num and core and state):
        return ""
    return "ad:%s%s|%s" % (num.group(1), core[:8], (state or "").lower())


def geo_key(lat, lng):
    if lat is None or lng is None:
        return ""
    try:
        return "ge:%.4f,%.4f" % (round(float(lat), 4), round(float(lng), 4))   # ~11 m cell
    except (TypeError, ValueError):
        return ""


def name_block(name, city, state):
    n = re.sub(r"[^a-z0-9]", "", (name or "").lower())[:20]
    return "nm:%s|%s|%s" % (n, re.sub(r"[^a-z0-9]", "", (city or "").lower())[:12], (state or "").lower())


def strong_keys(rec):
    """The keys that safely COLLAPSE two source-records into one outlet."""
    return [k for k in (phone_key(rec.get("phone")),
                        addr_key(rec.get("street"), rec.get("city"), rec.get("state")),
                        geo_key(rec.get("lat"), rec.get("lng"))) if k]


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def resolve(records):
    """records: list of dicts each with an 'rid' (unique) + identity fields. Returns {rid -> cluster_id}.
    Union any two records that share a STRONG key (phone/address/geo); otherwise a record clusters alone
    (its own rid), and callers may still group leftovers by name_block for a provisional match."""
    uf = _UF()
    key_owner = {}
    for r in records:
        rid = r["rid"]
        uf.find(rid)
        for k in strong_keys(r):
            if k in key_owner:
                uf.union(rid, key_owner[k])
            else:
                key_owner[k] = rid
    return {r["rid"]: uf.find(r["rid"]) for r in records}
