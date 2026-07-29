#!/usr/bin/env python3
"""zcta.py — ZIP centroids, so a typed place name can reach a ZIP-keyed API.

The VIP brand locator (`vtinfo.search_zip`) is keyed by ZIP. The locator UI lets you type a PLACE
("Houston, TX"). Those two facts met in `/api/locator` like this:

    zc = re.sub(r"[^0-9]", "", request.args.get("zip", "")) or "33602"

"Houston, TX" contains no digits, so it became "", so it became **33602 — which is Tampa**. The user
searched Houston and got a map of Tampa, with no indication anything had been substituted. That is
the whole bug: not a lookup that failed, but a lookup that failed and then answered anyway.

So: resolve properly, and when we cannot, say so. This module is the resolve half — `city_centroid`
already turns a place into a point off the Census Gazetteer, and the same Gazetteer publishes ZCTAs
(ZIP Code Tabulation Areas, which are the 5-digit ZIPs) with their own centroids. Nearest ZCTA to the
city centroid is the ZIP. Deterministic, free, no geocoding API, same source of truth as the city
layer it hands off from.

    python3 unifyd/zcta.py build          # fetch + write the zcta_centroids reference table
    python3 unifyd/zcta.py "Houston, TX"  # what ZIP does this resolve to, and how
"""
import io
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

_GAZ = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/"
        "2023_Gaz_zcta_national.zip")
_REF_TABLE = "zcta_centroids"
_CACHE = []                                                # [(zip, lat, lng)] — module-global


def _download(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "hoodie-mdm/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def build_reference(log=print):
    """Fetch the ZCTA Gazetteer and write `zcta_centroids`. ~33k rows, one per 5-digit ZIP."""
    try:
        raw = _download(_GAZ)
    except Exception as e:                                # noqa: BLE001
        log("[zcta] download failed: %s" % str(e)[:110])
        return 0
    zf = zipfile.ZipFile(io.BytesIO(raw))
    lines = zf.read(zf.namelist()[0]).decode("latin-1").splitlines()
    hdr = [h.strip() for h in lines[0].split("\t")]
    idx = {h: i for i, h in enumerate(hdr)}
    # The ZCTA file's key column has been GEOID and ZCTA5 in different vintages; accept either rather
    # than pinning one and silently parsing zero rows on the next release.
    gi = idx.get("GEOID", idx.get("ZCTA5"))
    la, lo = idx.get("INTPTLAT"), idx.get("INTPTLONG", idx.get("INTPTLONG "))
    if gi is None or la is None or lo is None:
        log("[zcta] unrecognized header %s — refusing to write a partial table" % hdr[:6])
        return 0
    rows = []
    for ln in lines[1:]:
        f = ln.split("\t")
        if len(f) <= max(gi, la, lo):
            continue
        try:
            z = f[gi].strip().zfill(5)
            rows.append({"zip": z, "lat": float(f[la]), "lng": float(f[lo].strip())})
        except (ValueError, IndexError):
            continue
    if not rows:
        log("[zcta] parsed 0 rows — aborting (keeping any existing table)")
        return 0
    warehouse.write_parquet(_REF_TABLE, rows, fields=["zip", "lat", "lng"])
    log("[zcta] wrote %s: %d ZIP centroids" % (_REF_TABLE, len(rows)))
    del _CACHE[:]
    return len(rows)


def _load():
    if _CACHE:
        return _CACHE
    try:
        for r in warehouse.query(_REF_TABLE, "SELECT zip, lat, lng FROM t"):
            _CACHE.append((r["zip"], r["lat"], r["lng"]))
    except Exception:                                     # noqa: BLE001 — absent table is not fatal
        pass
    return _CACHE


def nearest(lat, lng):
    """(zip, miles) for the ZCTA whose centroid is closest, or None if the table isn't built.

    None means "I could not tell you", and every caller must treat it that way — the bug this module
    exists to fix was a caller turning exactly this condition into a confident wrong answer.
    """
    rows = _load()
    if not rows or lat is None or lng is None:
        return None
    import locator_signal
    best, bestd = None, None
    for z, la, lo in rows:
        d = locator_signal.haversine_mi((lat, lng), (la, lo))
        if bestd is None or d < bestd:
            best, bestd = z, d
    return (best, round(bestd, 1)) if best else None


def center_of(zip_code):
    """(lat, lng) for a 5-digit ZIP, or None when the reference isn't built / the ZIP is unknown.

    The account layer needs a POINT to measure a radius from, so a resolve() that returns only a ZIP
    string can't drive it. None here must stay None at the caller — an unknown ZIP that quietly
    became a default point is the same bug this module was written to kill, one layer up.
    """
    z = (zip_code or "").strip().zfill(5)
    for zz, la, lo in _load():
        if zz == z:
            return (la, lo)
    return None


_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def resolve(text, log=print):
    """Turn whatever the user typed into {zip, how, label, lat, lng} — or {error} if we cannot.

    Accepts a bare ZIP ("77002"), a ZIP inside a string ("Houston TX 77002"), or a place
    ("Houston, TX"). There is deliberately NO default: a locator that quietly answers for a city you
    did not ask about is worse than one that says it doesn't know where you mean.
    """
    text = (text or "").strip()
    if not text:
        return {"error": "no location given"}

    m = _ZIP_RE.search(text)
    if m:
        return {"zip": m.group(1), "how": "zip", "label": m.group(1)}

    # "Houston, TX" / "Houston TX" -> city + state
    city, state = text, ""
    if "," in text:
        city, _, state = text.partition(",")
    else:
        parts = text.split()
        if len(parts) > 1 and len(parts[-1]) == 2 and parts[-1].isalpha():
            city, state = " ".join(parts[:-1]), parts[-1]
    city, state = city.strip(), state.strip().upper()
    if not state:
        return {"error": "need a state — try \"%s, TX\" or a 5-digit ZIP" % city}

    import city_centroid
    c = city_centroid.centroid(city, state)
    if not c:
        # "this city is not in the reference" and "there IS no reference" produce the same None, and
        # telling a user their real city can't be placed when the truth is that a table was never
        # built sends them to fix the wrong thing — and hides a broken deploy indefinitely.
        if not city_centroid._load():
            return {"error": "city reference not built — run `python3 unifyd/city_centroid.py` "
                             "(a 5-digit ZIP works meanwhile)"}
        return {"error": "couldn't place \"%s, %s\" — try a 5-digit ZIP" % (city, state)}
    lat, lng = (c[0], c[1]) if isinstance(c, (list, tuple)) else (c["lat"], c["lng"])

    near = nearest(lat, lng)
    if not near:
        return {"error": "ZIP reference not built — run `python3 unifyd/zcta.py build`",
                "lat": lat, "lng": lng}
    return {"zip": near[0], "how": "city", "label": "%s, %s" % (city.title(), state),
            "lat": lat, "lng": lng, "zip_distance_mi": near[1]}


def main(argv):
    if argv and argv[0] == "build":
        return 0 if build_reference() else 1
    r = resolve(" ".join(argv) or "Houston, TX")
    print(r)
    return 0 if r.get("zip") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
