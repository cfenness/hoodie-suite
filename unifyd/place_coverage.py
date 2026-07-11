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


# ── Bright Data SERP → Google Maps engine (no Places key needed; uses the BD creds we already hold). Google
# Maps returns a stable `fid` per place (dedup id, like place_id) + a `category` tag array we derive a type
# from. ToS discipline unchanged: we compute in memory and persist only COUNTS + our own merchants' fids. ──
_OFF_CATS = {"liquor_store", "wine_store", "beer_store", "off_licence", "wine_wholesaler", "brewing_supply_store"}
_ON_CATS = {"bar", "cocktail_bar", "lounge", "pub", "sports_bar", "wine_bar", "brewery", "brewpub", "beer_garden",
            "night_club", "gastropub", "izakaya", "tiki_bar", "dive_bar", "beer_hall", "taproom", "bar_and_grill",
            "lounge_bar", "wine_cellar", "distillery"}
_MAPS_TERMS = ["liquor store", "wine and spirits", "bar", "cocktail bar", "brewery", "night club"]


def _bd_key():
    return json.load(open(os.path.expanduser(
        "~/Library/Application Support/brightdata-cli/credentials.json")))["api_key"]


def _maps_type(categories):
    ids = {(c.get("id") or "").lower() for c in (categories or [])}
    if ids & _OFF_CATS:
        return "liquor_store"
    if ids & _ON_CATS:
        return "bar"
    return ""


def _bd_maps(query, lat, lon, key, zoom=13):
    import urllib.parse, urllib.request
    url = ("https://www.google.com/maps/search/%s/@%.4f,%.4f,%dz?brd_json=1&hl=en&gl=us"
           % (urllib.parse.quote(query), lat, lon, zoom))
    body = json.dumps({"zone": "cli_unlocker", "url": url, "format": "raw"}).encode()
    r = urllib.request.Request("https://api.brightdata.com/request", data=body,
                               headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    return (json.loads(urllib.request.urlopen(r, timeout=75).read()) or {}).get("organic", []) or []


def bd_universe(market, key=None, points=None, log=print):
    """Tiled Google-Maps-via-BD over the grid -> {fid: {norm, type, addr}} (in-memory real-world universe)."""
    key = key or _bd_key()
    grid = geo.MARKETS[market]
    if points:
        grid = grid[:points]
    uni = {}
    for i, (lat, lon) in enumerate(grid):
        for term in _MAPS_TERMS:
            try:
                for p in _bd_maps(term, lat, lon, key):
                    fid, t = p.get("fid"), _maps_type(p.get("category"))
                    if not fid:
                        continue
                    if fid not in uni:
                        uni[fid] = {"norm": _norm(p.get("title")), "type": t, "addr": p.get("address", "")}
                    elif not uni[fid]["type"] and t:
                        uni[fid]["type"] = t
            except Exception:
                pass
            time.sleep(0.15)
        if (i + 1) % 6 == 0:
            log("  [maps] %d/%d pins — %d distinct alcohol places" % (i + 1, len(grid), len(uni)))
    return uni


def _bd_maps_text(query, key):
    import urllib.parse, urllib.request
    url = "https://www.google.com/maps/search/%s/?brd_json=1&hl=en&gl=us" % urllib.parse.quote(query)
    body = json.dumps({"zone": "cli_unlocker", "url": url, "format": "raw"}).encode()
    r = urllib.request.Request("https://api.brightdata.com/request", data=body,
                               headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    return (json.loads(urllib.request.urlopen(r, timeout=75).read()) or {}).get("organic", []) or []


def enrich_hours(market="orlando", near="Orlando FL", key=None, log=print):
    """Grab open hours + address + phone + google_fid for every merchant from Google Maps — factual, refreshable
    fields (ToS: keep the durable id, refresh the rest). Lands <market>_outlet_hours; enriches the outlet master
    and doubles as the google_fid match key. Works for on- AND off-premise (all outlets have a Maps listing)."""
    key = key or _bd_key()
    merchants = warehouse.query("%s_merchants" % market, "SELECT store_id, name, type, is_chain FROM t")
    run_id = "hours-" + time.strftime("%Y%m%d-%H%M%S")
    rows = []
    for i, m in enumerate(merchants):
        try:
            ps = _bd_maps_text("%s %s" % (m["name"], near), key)
            p = ps[0] if ps else {}
        except Exception:
            p = {}
        rows.append(dict(account=m["name"], store_id=m.get("store_id", ""), type=m.get("type", ""),
                         is_chain=m.get("is_chain"), hours=json.dumps(p.get("open_hours") or {}),
                         address=p.get("address", ""), phone=p.get("phone", ""), rating=p.get("rating"),
                         google_fid=p.get("fid", ""), market=market, run_id=run_id))
        if (i + 1) % 25 == 0:
            warehouse.write_parquet("%s_outlet_hours" % market, rows)          # checkpoint
            log("  [hours] ...%d/%d" % (i + 1, len(merchants)))
    warehouse.write_parquet("%s_outlet_hours" % market, rows)
    got = sum(1 for r in rows if r["hours"] != "{}")
    fids = sum(1 for r in rows if r["google_fid"])
    log("[hours] %s: %d/%d with hours · %d with google_fid -> %s_outlet_hours"
        % (market, got, len(rows), fids, market))
    return rows


def match_by_name(merchants, universe):
    """Match OUR merchants to the Google universe by normalized name (exact, then loose contains)."""
    unorm = {}
    for fid, v in universe.items():
        unorm.setdefault(v["norm"], fid)
    for m in merchants:
        n = _norm(m.get("name"))
        fid = unorm.get(n)
        if not fid and len(n) >= 6:
            for un, f in unorm.items():
                if un and min(len(un), len(n)) >= 6 and (un in n or n in un):
                    fid = f
                    break
        m["google_fid"] = fid or ""
        m["google_confirmed"] = bool(fid)
    return merchants


def _official_universe(market, key, _nearby=None, log=print):
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


def run(market="orlando", key=None, points=None, log=print, persist=True):
    """Coverage via Bright Data SERP -> Google Maps (no Places key). Matches our merchants to the Google
    alcohol universe by fid, reports two-way coverage per type. Persists counts + our merchants' fids only."""
    merchants = warehouse.query("%s_merchants" % market, "SELECT * FROM t")
    if not merchants:
        return {"status": "no-merchants", "detail": "run doordash_geo --market %s first" % market}

    universe = bd_universe(market, key, points=points, log=log)      # real-world alcohol outlets Google knows
    match_by_name(merchants, universe)                              # our merchants -> fid

    liq = {f for f, v in universe.items() if v["type"] == "liquor_store"}
    bars = {f for f, v in universe.items() if v["type"] == "bar"}
    our_ret = {m["google_fid"] for m in merchants if m["type"] == "retail" and m.get("google_fid")}
    our_res = {m["google_fid"] for m in merchants if m["type"] == "restaurant" and m.get("google_fid")}

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None

    rep = dict(
        market=market, engine="bd-serp-maps",
        our_merchants=len(merchants),
        our_retail=sum(1 for m in merchants if m["type"] == "retail"),
        our_restaurant=sum(1 for m in merchants if m["type"] == "restaurant"),
        our_confirmed=sum(1 for m in merchants if m.get("google_confirmed")),
        google_alcohol_places=len(universe),
        google_liquor_stores=len(liq),
        google_bars_clubs=len(bars),
        matched_retail=len(our_ret & liq),
        matched_onpremise=len(our_res & bars),
        retail_coverage_pct=pct(len(our_ret & liq), len(liq)),      # of Google's liquor stores, % on our DoorDash
        onpremise_coverage_pct=pct(len(our_res & bars), len(bars)),
        gap_retail=len(liq - our_ret),                              # on Google, not in our sweep
        gap_onpremise=len(bars - our_res),
        confirmed_rate_pct=pct(sum(1 for m in merchants if m.get("google_confirmed")), len(merchants)),
        run_id="cov-" + time.strftime("%Y%m%d-%H%M%S"))

    if persist:                                                     # counts summary + our merchants' fids only
        warehouse.write_parquet("%s_coverage" % market, [rep])
        warehouse.write_parquet("%s_merchants" % market, merchants)
    log("[coverage] %s: %d/%d merchants Google-confirmed (%s%%) · retail coverage %s%% of %d Google liquor "
        "stores · on-premise %s%% of %d Google bars/clubs · gap %d retail / %d on-premise"
        % (market, rep["our_confirmed"], rep["our_merchants"], rep["confirmed_rate_pct"],
           rep["retail_coverage_pct"], rep["google_liquor_stores"], rep["onpremise_coverage_pct"],
           rep["google_bars_clubs"], rep["gap_retail"], rep["gap_onpremise"]))
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="orlando")
    ap.add_argument("--points", type=int, default=0, help="limit grid tiles (smoke test)")
    a = ap.parse_args()
    r = run(a.market, points=a.points or None)
    print(json.dumps(r, indent=2))
