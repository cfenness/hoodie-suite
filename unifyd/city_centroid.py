#!/usr/bin/env python3
"""city_centroid.py — the FAST geo layer: place any outlet with a city+state at its city centroid, instantly.

The precise geocoders (Census address batch, aggregator page-fetch) are exact but slow and need a street
address or a fetchable store page. Most outlets land with only city+state (DoorDash: 100% city, 93% state) —
so this layer maps them the moment they arrive, $0, no fetch, from a static reference:

  city_centroids  <- the free US Census Gazetteer (places + county subdivisions), state|city -> lat/lng.

Every outlet then carries a `geo_precision`: 'city' (this layer, ~city-accurate dot), 'exact' (a real
geocode/page fetch), 'city_miss'/'agg_miss' (tried, nothing found — don't retry forever). The precise passes
only ever UPGRADE a 'city' row to 'exact'; they never re-touch an 'exact' one. This is the layer that makes
"every outlet maps on the Coverage page" true immediately, with the exact crawl sharpening it over time.

geo_enrich_rows() is the INGESTION hook — call it in any sitemap/refresh path right before the write so no
outlet is ever taken in unplaced. fast_geo_pass() is the batch/registry entry that drains the existing backlog.
Reference build: build_reference() (stdlib urllib, run once / refresh yearly).
"""
import io
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

# Census 2023 National Gazetteer — Places (incorporated + CDP) and County Subdivisions (townships etc.).
# Tab-separated; the two we need are NAME + INTPTLAT/INTPTLONG (internal-point = centroid).
_GAZ = {
    "place": "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip",
    "cousub": "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_cousubs_national.zip",
}
_REF_TABLE = "city_centroids"
_CACHE = {}                                                # {(STATE, citykey): (lat, lng)} — module-global

# strip the LSAD noise so "St. Louis city" / "Nashville-Davidson metropolitan government" match "st louis" etc.
_SUFFIX = re.compile(r"\b(city|town|village|borough|municipality|cdp|township|charter|metropolitan|"
                     r"government|balance|comunidad|zona urbana|urbana)\b", re.I)


def _norm(name):
    n = (name or "").lower()
    n = _SUFFIX.sub(" ", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)                     # drop periods/hyphens/apostrophes
    return re.sub(r"\s+", " ", n).strip()


def _download(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "hoodie-mdm/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def build_reference(log=print):
    """Fetch the Gazetteer, parse city/township centroids, write the `city_centroids` reference table.
    Places win over county-subdivisions on a name collision (incorporated cities are the better centroid)."""
    seen, rows = set(), []
    # cousubs first, places second → places overwrite (dedupe keeps the LAST, so append places after)
    for kind in ("cousub", "place"):
        try:
            raw = _download(_GAZ[kind])
        except Exception as e:
            log("[city-centroid] download %s failed: %s" % (kind, str(e)[:100]))
            continue
        zf = zipfile.ZipFile(io.BytesIO(raw))
        text = zf.read(zf.namelist()[0]).decode("latin-1")
        lines = text.splitlines()
        hdr = [h.strip() for h in lines[0].split("\t")]
        idx = {h: i for i, h in enumerate(hdr)}
        us, nm = idx.get("USPS"), idx.get("NAME")
        la = idx.get("INTPTLAT"); lo = idx.get("INTPTLONG", idx.get("INTPTLONG "))
        for ln in lines[1:]:
            f = ln.split("\t")
            if len(f) <= max(us, nm, la, lo):
                continue
            try:
                st = f[us].strip().upper(); ck = _norm(f[nm])
                lat = float(f[la]); lng = float(f[lo].strip())
            except (ValueError, IndexError):
                continue
            if not st or not ck:
                continue
            rows.append({"state": st, "city": ck, "lat": lat, "lng": lng, "kind": kind})
        log("[city-centroid] parsed %s: %d cumulative rows" % (kind, len(rows)))
    if not rows:
        log("[city-centroid] no reference rows parsed — aborting (keeping any existing table)")
        return 0
    warehouse.write_parquet(_REF_TABLE, rows, fields=["state", "city", "lat", "lng", "kind"])
    log("[city-centroid] wrote %s: %d city centroids" % (_REF_TABLE, len(rows)))
    _CACHE.clear()
    return len(rows)


def _load():
    if _CACHE:
        return _CACHE
    try:
        for r in warehouse.query(_REF_TABLE, "SELECT state, city, lat, lng FROM t"):
            _CACHE[(r["state"], r["city"])] = (r["lat"], r["lng"])
    except Exception:
        pass
    return _CACHE


def centroid(city, state):
    """(lat, lng) for a city+state, or None. State may be a 2-letter code; city is fuzzily normalized."""
    if not city or not state:
        return None
    return _load().get((str(state).strip().upper(), _norm(city)))


def geo_enrich_rows(rows, log=None):
    """INGESTION hook: for every row that has city+state but no lat, drop in the city centroid and stamp
    geo_precision='city' (misses stamped 'city_miss' so the exact crawl still knows they were seen). Mutates
    and returns `rows`. Rows already carrying lat or an exact geo_precision are left untouched. Cheap + in-proc
    — call it right before write_accumulate so nothing is ever taken in unplaced."""
    hit = 0
    for r in rows:
        if r.get("lat") not in (None, "") or r.get("geo_precision") in ("exact", "city"):
            continue
        c = centroid(r.get("city"), r.get("state"))
        if c:
            r["lat"], r["lng"] = c[0], c[1]
            r["geo_precision"] = "city"
            hit += 1
        elif r.get("city"):
            r["geo_precision"] = "city_miss"
    if log:
        log("[city-centroid] enriched %d/%d rows inline" % (hit, len(rows)))
    return rows


def fast_geo_pass(limit=None, log=print):
    """Drain the existing backlog: city-centroid the next batch of un-geocoded src_outlets that have a
    city+state, write lat/lng + geo_precision back. Bounded (FAST_GEO_LIMIT). No fetch — the whole batch is a
    dictionary lookup; the cost is the full-table merge, so run it in big chunks."""
    limit = limit if limit is not None else int(os.environ.get("FAST_GEO_LIMIT", "250000"))
    import refresh_fast
    _load()
    rows = warehouse.query(
        "src_outlets",
        "SELECT * FROM t WHERE lat IS NULL AND city IS NOT NULL AND city <> '' "
        "AND state IS NOT NULL AND state <> '' "
        "AND (geo_precision IS NULL OR geo_precision NOT IN ('exact', 'city', 'city_miss')) "
        "LIMIT %d" % limit)
    if not rows:
        log("[fast-geo] no un-geocoded city+state src_outlets — done")
        return 0
    out = []
    for r in rows:
        d = dict(r)
        c = centroid(d.get("city"), d.get("state"))
        if c:
            d["lat"], d["lng"] = c[0], c[1]
            d["geo_precision"] = "city"
        else:
            d["geo_precision"] = "city_miss"
        out.append({k: d.get(k) for k in refresh_fast.FLD})
    got = sum(1 for o in out if o.get("geo_precision") == "city")
    warehouse.write_accumulate("src_outlets", out, key=lambda r: (r["source"], r["store_id"]),
                               fields=refresh_fast.FLD)
    log("[fast-geo] +%d city-mapped of %d (%.0f%%) -> src_outlets" % (got, len(out), 100 * got / max(1, len(out))))
    return got


def run(log=print):
    # ensure the reference exists (build once), then drain a batch
    if not _load():
        build_reference(log=log)
    return fast_geo_pass(log=log)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        print("built %d centroids" % build_reference())
    else:
        print("city-mapped %d" % run())
