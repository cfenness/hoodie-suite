"""place_coverage.py — how complete is our DoorDash sweep? Measure it against Google Places (ToS-safe).

Google forbids STORING their content, but two things are allowed: derived aggregate COUNTS, and the durable
place_id. So we compute coverage in memory and persist only numbers + our own merchants' place_ids — never
Google's names/ratings for outlets we don't already have. Two anchors:
  · Google universe — tiled Nearby Search over the metro grid for alcohol place types (the real-world set).
  · Our merchants  — each confirmed by a Text Search -> place_id (proves it's a real outlet, not a ghost).
Coverage = |our place_ids ∩ Google universe| / |Google universe|, per type. The gap (on Google, not on
DoorDash) is reported as a COUNT only. (The FL DBPR license list we already hold is the free legal-universe
anchor — see coverage_vs_dbpr; it needs no API and is the better denominator for off-premise.)

    GOOGLE_MAPS_API_KEY=... python place_coverage.py --market orlando
"""
import argparse, json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import doordash_geo as geo                      # reuse the metro grid

_ALCOHOL_TYPES = ["liquor_store", "bar", "night_club"]   # 'restaurant' is too broad to be a denominator
_METRO = {"orlando": ("Orlando FL", 3000), "miami": ("Miami FL", 3000)}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _nearby_client(key):
    import requests
    def call(lat, lon, typ, radius):
        r = requests.post("https://places.googleapis.com/v1/places:searchNearby", timeout=20,
                          headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
                                   "X-Goog-FieldMask": "places.id,places.types"},
                          data=json.dumps({"includedTypes": [typ], "maxResultCount": 20,
                                           "locationRestriction": {"circle": {
                                               "center": {"latitude": lat, "longitude": lon}, "radius": radius}}}))
        r.raise_for_status()
        return (r.json() or {}).get("places") or []
    return call


def _text_client(key):
    import requests
    def call(q):
        r = requests.post("https://places.googleapis.com/v1/places:searchText", timeout=20,
                          headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
                                   "X-Goog-FieldMask": "places.id,places.types"},
                          data=json.dumps({"textQuery": q, "maxResultCount": 1}))
        r.raise_for_status()
        return (r.json() or {}).get("places") or []
    return call


def google_universe(market, key, _nearby=None, log=print):
    """Tiled Nearby Search over the grid -> {place_id: primary_type}. In-memory only (place_id is storable,
    but we keep the universe transient and persist just its counts)."""
    near = _nearby or _nearby_client(key)
    grid = geo.MARKETS[market]
    _, radius = _METRO.get(market, ("", 3000))
    universe = {}
    for i, (lat, lon) in enumerate(grid):
        for typ in _ALCOHOL_TYPES:
            try:
                for p in near(lat, lon, typ, radius):
                    pid = p.get("id")
                    if pid and pid not in universe:
                        universe[pid] = typ
            except Exception:
                pass
            time.sleep(0.05)
        if (i + 1) % 8 == 0:
            log("  [google] tiled %d/%d pins — %d distinct alcohol places" % (i + 1, len(grid), len(universe)))
    return universe


def confirm_merchants(merchants, market, key, _text=None):
    """Text-Search each of OUR merchants -> place_id (confirms a real outlet). Annotates in place."""
    text = _text or _text_client(key)
    metro = _METRO.get(market, ("", 0))[0]
    for m in merchants:
        try:
            ps = text("%s %s" % (m["name"], metro))
        except Exception:
            ps = []
        m["google_place_id"] = ps[0].get("id") if ps else ""
        m["google_types"] = "|".join(ps[0].get("types") or []) if ps else ""
        m["google_confirmed"] = bool(m.get("google_place_id"))
    return merchants


def run(market="orlando", key=None, log=print):
    key = key or os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        return {"status": "google-disabled", "detail": "GOOGLE_MAPS_API_KEY not set"}
    merchants = warehouse.query("%s_merchants" % market, "SELECT * FROM t")
    if not merchants:
        return {"status": "no-merchants", "detail": "run doordash_geo --market %s first" % market}

    universe = google_universe(market, key, log=log)                 # real-world alcohol outlets Google knows
    confirm_merchants(merchants, market, key)                        # our merchants -> place_id

    upids = {"liquor_store": {p for p, t in universe.items() if t == "liquor_store"},
             "bar": {p for p, t in universe.items() if t in ("bar", "night_club")}}
    our_retail = {m["google_place_id"] for m in merchants if m["type"] == "retail" and m.get("google_place_id")}
    our_rest = {m["google_place_id"] for m in merchants if m["type"] == "restaurant" and m.get("google_place_id")}

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None

    rep = dict(
        market=market,
        our_merchants=len(merchants),
        our_retail=sum(1 for m in merchants if m["type"] == "retail"),
        our_restaurant=sum(1 for m in merchants if m["type"] == "restaurant"),
        our_confirmed=sum(1 for m in merchants if m.get("google_confirmed")),
        google_liquor_stores=len(upids["liquor_store"]),
        google_bars_clubs=len(upids["bar"]),
        matched_retail=len(our_retail & upids["liquor_store"]),
        matched_onpremise=len(our_rest & upids["bar"]),
        retail_coverage_pct=pct(len(our_retail & upids["liquor_store"]), len(upids["liquor_store"])),
        onpremise_coverage_pct=pct(len(our_rest & upids["bar"]), len(upids["bar"])),
        gap_retail=len(upids["liquor_store"] - our_retail),          # on Google, not on our DoorDash sweep
        gap_onpremise=len(upids["bar"] - our_rest),
        confirmed_rate_pct=pct(sum(1 for m in merchants if m.get("google_confirmed")), len(merchants)),
        run_id="cov-" + time.strftime("%Y%m%d-%H%M%S"))

    # persist: counts summary (safe) + our merchants annotated with the durable place_id (ToS-allowed)
    warehouse.write_parquet("%s_coverage" % market, [rep])
    warehouse.write_parquet("%s_merchants" % market,
                            [{k: v for k, v in m.items() if k != "google_types"} for m in merchants])
    log("[coverage] %s: %d/%d merchants Google-confirmed (%.0f%%) · retail coverage %s%% of %d Google liquor "
        "stores · on-premise %s%% of %d Google bars/clubs · gap %d retail / %d on-premise"
        % (market, rep["our_confirmed"], rep["our_merchants"], rep["confirmed_rate_pct"] or 0,
           rep["retail_coverage_pct"], rep["google_liquor_stores"], rep["onpremise_coverage_pct"],
           rep["google_bars_clubs"], rep["gap_retail"], rep["gap_onpremise"]))
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="orlando")
    a = ap.parse_args()
    r = run(a.market)
    print(json.dumps(r, indent=2))
