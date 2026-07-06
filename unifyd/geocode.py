"""geocode.py — batch-geocode outlet addresses via the free US Census geocoder.

No API key. Batches addresses to geocoding.geo.census.gov/geocoder/geographies/addressbatch and
returns lat/lng + 5-digit county FIPS (state+county) — which also FIXES the FL numeric-county gap
(FL 'Location County' is a code, not a name) and makes FL census-joinable like TX, in one pass.
~80% match rate on US street addresses; unmatched rows get blank coords (no map dot).

`geocode_outlets(header, rows)` → (new_header, new_rows) with latitude/longitude/county_fips
appended, making any outlet dataset map-ready (Coverage Map) + census-joinable. Runs on the Mac
or Fly (network-bound, low memory). stdlib + requests.
"""
import csv, io, time

BATCH_URL = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
CHUNK = 9000                       # Census caps at 10k/batch; keep margin
DEFAULT_COLMAP = {"street": "address", "city": "city", "state": "state", "zip": "zip"}


def geocode_batch(records, log=print, retries=2):
    """records: iterable of (id, street, city, state, zip) — up to ~10k. Returns
    {id: {matched, lat, lng, county_fips}} (county_fips = 5-digit state+county, '' if unmatched)."""
    import requests
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in records:
        w.writerow([r[0], r[1], r[2], r[3], r[4]])
    files = {"addressFile": ("addr.csv", buf.getvalue(), "text/csv")}
    data = {"benchmark": "Public_AR_Current", "vintage": "Current_Current"}
    for attempt in range(retries + 1):
        try:
            resp = requests.post(BATCH_URL, files=files, data=data, timeout=600)
            out = {}
            for row in csv.reader(io.StringIO(resp.text)):
                if len(row) < 3:
                    continue
                rid = row[0]
                if row[2] != "Match" or len(row) < 10:
                    out[rid] = {"matched": False}
                    continue
                # id,input,status,type,matched,"lng,lat",tiger,side,stateFIPS,countyFIPS,tract,block
                try:
                    lng, lat = row[5].split(",")
                    out[rid] = {"matched": True, "lat": float(lat), "lng": float(lng),
                                "county_fips": (row[8] + row[9]) if row[8] and row[9] else ""}
                except Exception:
                    out[rid] = {"matched": False}
            return out
        except Exception as e:
            if attempt < retries:
                time.sleep(3); continue
            log("geocode batch failed: %s" % str(e)[:140])
            return {}


def geocode_outlets(header, rows, colmap=None, log=print, cap=200000):
    """Append latitude / longitude / county_fips to outlet rows using the address columns.
    colmap maps our fields (street/city/state/zip) → this dataset's column names; defaults to the
    master schema. Returns (new_header, new_rows, stats)."""
    idx = {str(h).lower(): i for i, h in enumerate(header)}
    cm = colmap or DEFAULT_COLMAP
    def gi(k):
        return idx.get((cm.get(k) or "").lower(), -1)
    si, ci, sti, zi = gi("street"), gi("city"), gi("state"), gi("zip")
    if si < 0:
        log("no address column (%s); cannot geocode" % cm.get("street"))
        return header, rows, {"matched": 0, "requested": 0}
    use = rows[:cap]
    recs = []
    for i, r in enumerate(use):
        street = r[si] if si < len(r) else ""
        if street:
            recs.append((str(i), street,
                         r[ci] if 0 <= ci < len(r) else "",
                         r[sti] if 0 <= sti < len(r) else "",
                         r[zi] if 0 <= zi < len(r) else ""))
    results = {}
    for j in range(0, len(recs), CHUNK):
        results.update(geocode_batch(recs[j:j + CHUNK], log=log))
        log("geocoded %d/%d …" % (min(j + CHUNK, len(recs)), len(recs)))
        time.sleep(1)
    new_header = list(header) + ["latitude", "longitude", "county_fips"]
    new_rows = []
    for i, r in enumerate(use):
        g = results.get(str(i))
        if g and g.get("matched"):
            new_rows.append(list(r) + [g["lat"], g["lng"], g.get("county_fips", "")])
        else:
            new_rows.append(list(r) + ["", "", ""])
    matched = sum(1 for g in results.values() if g.get("matched"))
    log("geocode: %d/%d matched (%.0f%%)" % (matched, len(recs), 100 * matched / max(1, len(recs))))
    return new_header, new_rows, {"matched": matched, "requested": len(recs)}


def main():
    # smoke test against the live Census geocoder
    recs = [("1", "233 S Wacker Dr", "Chicago", "IL", "60606"),
            ("2", "100 Congress Ave", "Austin", "TX", "78701"),
            ("3", "801 W Bay Dr", "Largo", "FL", "33770")]
    r = geocode_batch(recs)
    for rid, g in sorted(r.items()):
        print(rid, g)


if __name__ == "__main__":
    main()
