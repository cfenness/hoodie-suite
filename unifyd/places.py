"""places.py — the restaurant / on-premise-accounts connector (Orlando first).

Spine = the AUTHORITATIVE, storable, free source: Florida DBPR/ABT alcohol-beverage
licenses (the same extracts the engine already pulls in `fl-outlets`). We filter to
Orange County (Orlando), normalize each license into a canonical ACCOUNT record, and
classify on-/off-premise by license series. Result lands as Parquet in the warehouse
(Tigris or local) — see warehouse.py.

Enrichment layers (added on top of the spine, never replacing it):
  - open POI (Overture / Foursquare OS) — category, geo, hours — matched by name+ZIP.
  - Google Places — ToS-safe, ON DEMAND, key-gated: we store the durable `place_id` and
    fetch other fields live/refreshable; we do NOT bulk-warehouse Google content.

Design mirrors the other scrapers: pure, testable functions + a `pull()` that returns a
run record and self-reports `degraded` with warnings when the source schema drifts.
"""
import csv, io, os, time, urllib.request

FL_BASE = "https://www2.myfloridalicense.com/sto/file_download/extracts"
# The ABT license extracts (header-less; use FL_HEADER). Same files as the engine's fl-outlets.
FL_ABT_EXTRACTS = ["bd4006lic", "bd4005lic", "bd4002lic"]
FL_HEADER = ["Board", "Profession", "Owner Name", "Series", "Modifier", "Mail Address 1", "Mail Address 2",
    "Mail Address 3", "Mail City", "Mail State", "Mail ZIP", "Mail County", "DBA", "Location Address 1",
    "Location Address 2", "Location Address 3", "Location City", "Location State", "Location ZIP",
    "Location County", "License Number", "Primary Status", "Secondary Status", "Original Licensure Date",
    "Effective Date", "Expiration Date", "Tax Stamp Designation", "Smoking Designation", "Retail Tobacco Indicator"]

# Canonical account schema (stable Parquet columns). Enrichment fields start null.
FIELDS = ["account_id", "name", "owner", "series", "premise", "addr", "city", "county",
          "state", "zip", "status", "licensed_since", "source", "pulled_at",
          "lat", "lng", "category", "google_place_id", "rating", "price_level", "phone", "poi_source"]

ORLANDO_COUNTY = "ORANGE"     # Orlando, FL is Orange County


def _col(header):
    """Case-insensitive header -> index map, tolerant of stray whitespace."""
    return {h.strip().lower(): i for i, h in enumerate(header)}


def classify_premise(series, modifier=""):
    """On-/off-premise from the ABT license series. 'COP' = Consumption On Premises;
    'APS' = package (off); SFS/SRX/SBX = special restaurant (on). Unknown -> 'unknown'
    (never dropped — we keep the row and let queries decide)."""
    s = ((series or "") + " " + (modifier or "")).upper()
    if any(k in s for k in ("COP", "SFS", "SRX", "SBX", "CATERING")):
        return "on"
    if "APS" in s:
        return "off"
    return "unknown"


def normalize(rows, header=None):
    """FL ABT license rows -> canonical account dicts. Returns (records, warnings).
    Schema-tolerant: maps by header name and self-reports if key columns are missing."""
    header = header or FL_HEADER
    ix = _col(header)
    warnings = []
    need = ["dba", "owner name", "location county", "license number"]
    missing = [n for n in need if n not in ix]
    if missing:
        warnings.append("unmapped columns: %s" % ", ".join(missing))
    def g(row, key):
        i = ix.get(key)
        return (row[i].strip() if i is not None and i < len(row) and row[i] else "")
    out = []
    for row in rows:
        if not any((c or "").strip() for c in row):
            continue
        dba, owner = g(row, "dba"), g(row, "owner name")
        addr = " ".join(p for p in (g(row, "location address 1"), g(row, "location address 2")) if p)
        series = g(row, "series")
        out.append({
            "account_id": g(row, "license number") or None,
            "name": dba or owner or None,
            "owner": owner or None,
            "series": (series + g(row, "modifier")).strip() or None,
            "premise": classify_premise(series, g(row, "modifier")),
            "addr": addr or None,
            "city": g(row, "location city") or None,
            "county": (g(row, "location county") or "").upper() or None,
            "state": g(row, "location state") or None,
            "zip": (g(row, "location zip") or "")[:5] or None,
            "status": g(row, "primary status") or None,
            "licensed_since": g(row, "original licensure date") or None,
            "source": "fl-dbpr-abt",
            "pulled_at": None,
            "lat": None, "lng": None, "category": None, "google_place_id": None,
            "rating": None, "price_level": None, "phone": None, "poi_source": None,
        })
    return out, warnings


def filter_market(records, county=ORLANDO_COUNTY):
    """Keep accounts in the target county (Orlando = ORANGE)."""
    c = (county or "").upper()
    return [r for r in records if (r.get("county") or "") == c]


def dedupe(records):
    """One row per license number; fall back to name+addr when the id is blank."""
    seen, out = set(), []
    for r in records:
        k = r.get("account_id") or ((r.get("name") or "") + "|" + (r.get("addr") or ""))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def _fetch_fl(eid):
    return urllib.request.urlopen("%s/%s.csv" % (FL_BASE, eid), timeout=180).read().decode("utf-8", "replace")


def pull(county=ORLANDO_COUNTY, extracts=None, fetch=None, store=None, pulled_at=None):
    """Pull -> normalize -> filter-to-market -> dedupe -> write Parquet. Returns a run
    record (rows, delta n/a here, status, degraded warnings, premise breakdown).
    `fetch(eid)->csv_text` and `store(name, records, fields)->{rows,uri}` are injectable
    (defaults hit Florida DBPR and warehouse.write_parquet), so this tests offline."""
    fetch = fetch or _fetch_fl
    extracts = extracts or FL_ABT_EXTRACTS
    pulled_at = pulled_at if pulled_at is not None else int(time.time())
    started = int(time.time() * 1000)
    warnings, all_recs, per = [], [], []
    for eid in extracts:
        try:
            txt = fetch(eid)
            rows = list(csv.reader(io.StringIO(txt)))
            recs, w = normalize(rows, FL_HEADER)
            warnings += ["%s: %s" % (eid, m) for m in w]
            per.append({"id": eid, "rows": len(recs), "status": "success"})
            all_recs += recs
        except Exception as e:
            warnings.append("%s: fetch/parse failed: %s" % (eid, str(e)[:160]))
            per.append({"id": eid, "rows": 0, "status": "failed"})

    market = dedupe(filter_market(all_recs, county))
    for r in market:
        r["pulled_at"] = pulled_at

    # Self-heal signal: a populated pull that yields zero in-market rows, or unmapped
    # columns, is degraded (source drift) rather than silently-empty good data.
    degraded = bool(warnings) or (all_recs and not market)
    if all_recs and not market:
        warnings.append("0 rows in county %s from %d parsed — check county column / value" % (county, len(all_recs)))

    breakdown = {}
    for r in market:
        breakdown[r["premise"]] = breakdown.get(r["premise"], 0) + 1

    store = store or (lambda name, recs, fields: __import__("warehouse").write_parquet(name, recs, fields))
    written = store("orlando_accounts", market, FIELDS) if market else {"rows": 0, "uri": None}

    fin = int(time.time() * 1000)
    return {
        "id": "R-places-%d" % (pulled_at % 100000), "connId": "orlando-accounts",
        "startedAt": started, "finishedAt": fin, "durationMs": fin - started,
        "status": ("degraded" if degraded else ("failed" if not market else "success")),
        "county": county, "total": written["rows"], "uri": written["uri"],
        "premise": breakdown, "extracts": per, "warnings": warnings,
    }


# --------------------------------------------------------------- enrichment (scaffold) ----
def match_open_poi(records, poi_records):
    """Fill category/geo from an open POI iterable, matched by normalized name + ZIP.
    poi_records: dicts with name, zip, category, lat, lng. Returns count matched."""
    def norm(s): return "".join(ch for ch in (s or "").lower() if ch.isalnum())
    index = {}
    for p in poi_records:
        index.setdefault((norm(p.get("name")), (p.get("zip") or "")[:5]), p)
    n = 0
    for r in records:
        p = index.get((norm(r.get("name")), (r.get("zip") or "")[:5]))
        if p:
            r["lat"], r["lng"] = p.get("lat"), p.get("lng")
            r["category"] = p.get("category")
            r["poi_source"] = p.get("source", "open-poi")
            n += 1
    return n


def enrich_google(records, api_key=None, _client=None, limit=None):
    """ON-DEMAND, ToS-safe Google Places enrichment. Stores only the durable place_id on
    the record; other fields (rating, price, phone) are fetched live and may be refreshed,
    never bulk-warehoused. Disabled without a key (returns a note, no-op). `_client(name,
    addr)->{place_id,rating,price_level,phone}` is injectable for tests."""
    key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key and _client is None:
        return {"status": "google-disabled", "matched": 0, "detail": "GOOGLE_MAPS_API_KEY not set"}
    client = _client or _google_places_client(key)
    matched = 0
    todo = [r for r in records if not r.get("google_place_id")]
    if limit:
        todo = todo[:limit]
    for r in todo:
        try:
            hit = client(r.get("name"), r.get("addr") + " " + (r.get("city") or "") + " FL")
        except Exception:
            hit = None
        if hit and hit.get("place_id"):
            r["google_place_id"] = hit["place_id"]        # durable id — safe to store
            r["rating"] = hit.get("rating")               # live fields — refreshable
            r["price_level"] = hit.get("price_level")
            r["phone"] = hit.get("phone")
            r["poi_source"] = r.get("poi_source") or "google"
            matched += 1
    return {"status": "ok", "matched": matched}


def _google_places_client(key):
    """Real Places Text Search client (built lazily; requests only imported here)."""
    def call(name, addr):
        import json, requests
        q = ((name or "") + " " + (addr or "")).strip()
        r = requests.post("https://places.googleapis.com/v1/places:searchText", timeout=15,
                          headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
                                   "X-Goog-FieldMask": "places.id,places.rating,places.priceLevel,places.internationalPhoneNumber"},
                          data=json.dumps({"textQuery": q, "maxResultCount": 1}))
        r.raise_for_status()
        places = (r.json() or {}).get("places") or []
        if not places:
            return None
        p = places[0]
        return {"place_id": p.get("id"), "rating": p.get("rating"),
                "price_level": p.get("priceLevel"), "phone": p.get("internationalPhoneNumber")}
    return call
