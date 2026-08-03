#!/usr/bin/env python3
"""metro_analytics.py — per-metro and per-NEIGHBOURHOOD market analytics, computed from the warehouse.

Sits directly on `outlet_geography` (geo_resolve.py), which is what made this possible: before it,
only 3.8% of accounts carried a county and a "New York metro" question answered 106 outlets against
a warehouse holding 39,167 geocoded accounts there.

Three layers, each DERIVED — no network, no new collection:

  metro_summary(cbsa)   the market: resident alcohol demand $, account universe, on/off-premise
                        split, chain vs independent, demand per door
  neighborhoods(cbsa)   the same at ZCTA grain, which is where the story is — `trade_area_demand`
                        already holds ZIP-level demand for 33,791 ZIPs, so every neighbourhood gets
                        a demand figure and an account count, and the RATIO ranks them
  whitespace(cbsa)      neighbourhoods carrying high resident demand against thin retail coverage

WHAT THE NUMBERS ARE, precisely — this matters because these figures go in front of customers:
  - demand $ is MODELLED, not observed: ACS household income distribution x BLS CEX mean spend per
    income bracket. It is a defensible estimate of what residents spend, not a sales feed. Every
    caller renders it labelled as derived.
  - account counts are OBSERVED: distinct accounts we hold, resolved to the neighbourhood by
    point-in-polygon. They are a floor, not a census — coverage varies by source and metro, so
    `coverage_note` reports which sources contributed and callers must not present a count as
    "every account in the ZIP".
  - demand-per-door DIVIDES a modelled numerator by an observed denominator. It is a comparative
    index across neighbourhoods in ONE metro, never an absolute dollar opportunity.

    python3 unifyd/metro_analytics.py --top 20
    python3 unifyd/metro_analytics.py --metro 35620 --neighborhoods
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

GEO = "outlet_geography"
DEMAND = "trade_area_demand"

# Below this many resolved accounts a neighbourhood's demand-per-door is noise, not a signal — one
# account in a dense ZIP produces a spectacular-looking and meaningless ratio. Same discipline as
# locator_signal's MIN_REF: under the floor, emit no verdict and say why.
MIN_ACCOUNTS_FOR_RATIO = int(os.environ.get("METRO_MIN_ACCOUNTS", "5"))


def _q(table, sql, params=None):
    try:
        return warehouse.query(table, sql, params)
    except Exception:
        return []


def top_metros(n=20):
    """The n largest metros by RESIDENT DEMAND (not by our account count — ranking by our own
    coverage would just rank where we happen to have scraped hardest)."""
    rows = _q(GEO, """
        SELECT cbsa_code, ANY_VALUE(cbsa_name) AS cbsa_name,
               COUNT(*) AS accounts,
               LIST(DISTINCT county_fips) AS counties
        FROM t WHERE cbsa_type = 'metro' AND cbsa_code IS NOT NULL
        GROUP BY cbsa_code""")
    if not rows:
        return []
    dem = {r["geo_fips"]: r for r in _q(DEMAND, "SELECT * FROM t WHERE geo_level = 'county'")}
    out = []
    for r in rows:
        hh = tot = away = home = 0.0
        for f in (r.get("counties") or []):
            d = dem.get(f)
            if not d:
                continue
            hh += float(d.get("households") or 0)
            tot += float(d.get("demand_total_usd") or 0)
            away += float(d.get("demand_away_usd") or 0)
            home += float(d.get("demand_at_home_usd") or 0)
        out.append({"cbsa_code": r["cbsa_code"], "cbsa_name": r["cbsa_name"],
                    "accounts": r["accounts"], "counties": len(r.get("counties") or []),
                    "households": int(hh), "demand_total": tot,
                    "demand_away": away, "demand_at_home": home,
                    "demand_per_hh": (tot / hh) if hh else 0.0})
    out.sort(key=lambda x: -x["demand_total"])
    return out[:n]


def metro_summary(cbsa):
    """One metro's market shape. Account attributes live on src_outlets, geography on
    outlet_geography, so this joins the two on source|store_id.

    The join runs in SQL on ONE connection rather than pulling both tables into Python: src_outlets
    is 1.77M rows and outlet_geography 888k, so a Python-side join would materialise the national
    table once per metro — 20 metros, 20 full scans, for a question that is a single filtered join."""
    con = warehouse.connect()
    if not (warehouse.attach_view(con, GEO, view="g") and warehouse.attach_view(con, "src_outlets", view="so")):
        return None
    # UNKNOWN IS NOT INDEPENDENT, AND UNKNOWN IS NOT "NO ALCOHOL". This started as
    # `SUM(CASE WHEN is_chain THEN 0 ELSE 1 END) AS independent`, which counts every LEFT-JOIN miss
    # and every NULL flag as an independent account — New York then reported "96% independent" off a
    # book that is 39,411 DoorDash rows, most of which simply carry no chain flag at all. A figure
    # like that in front of a customer is worse than no figure. Each attribute is therefore counted
    # in three buckets and the unknown one is reported, not folded into a claim.
    row = con.execute("""
        SELECT ANY_VALUE(g.cbsa_name) AS cbsa_name,
               COUNT(*) AS accounts,
               COUNT(DISTINCT g.county_fips) AS counties,
               COUNT(DISTINCT g.zcta) AS zips,
               COUNT(so.source) AS attributed,
               SUM(CASE WHEN COALESCE(so.f_beer,FALSE) OR COALESCE(so.f_wine,FALSE)
                             OR COALESCE(so.f_spirits,FALSE) THEN 1 ELSE 0 END) AS alcohol_accounts,
               SUM(CASE WHEN so.is_chain IS NULL THEN 0 WHEN so.is_chain THEN 1 ELSE 0 END) AS chain,
               SUM(CASE WHEN so.is_chain IS NULL THEN 0 WHEN so.is_chain THEN 0 ELSE 1 END) AS independent,
               SUM(CASE WHEN so.is_chain IS NULL THEN 1 ELSE 0 END) AS chain_unknown
        FROM g LEFT JOIN so
          ON so.source = g.source AND CAST(so.store_id AS VARCHAR) = g.store_id
        WHERE g.cbsa_code = ?""", [str(cbsa)]).fetchone()
    if not row or not row[1]:
        return None
    name, accounts, counties_n, zips, attributed, alc, chain, indie, chain_unknown = row

    srcs = dict(con.execute(
        "SELECT source, COUNT(*) n FROM g WHERE cbsa_code = ? GROUP BY 1 ORDER BY n DESC",
        [str(cbsa)]).fetchall())
    counties = [r[0] for r in con.execute(
        "SELECT DISTINCT county_fips FROM g WHERE cbsa_code = ? AND county_fips IS NOT NULL",
        [str(cbsa)]).fetchall()]

    dem = {r["geo_fips"]: r for r in _q(DEMAND, "SELECT * FROM t WHERE geo_level = 'county'")}
    hh = sum(float(dem[c].get("households") or 0) for c in counties if c in dem)
    tot = sum(float(dem[c].get("demand_total_usd") or 0) for c in counties if c in dem)
    away = sum(float(dem[c].get("demand_away_usd") or 0) for c in counties if c in dem)
    home = sum(float(dem[c].get("demand_at_home_usd") or 0) for c in counties if c in dem)

    return {"cbsa_code": str(cbsa), "cbsa_name": name,
            "accounts": accounts, "counties": counties_n, "zips": zips,
            "attributed": attributed or 0,
            "alcohol_accounts": alc or 0,
            "chain": chain or 0, "independent": indie or 0, "chain_unknown": chain_unknown or 0,
            "households": int(hh), "demand_total": tot, "demand_away": away, "demand_at_home": home,
            "demand_per_hh": (tot / hh) if hh else 0.0,
            "demand_per_account": (tot / accounts) if accounts else 0.0,
            "sources": srcs,
            "coverage_note": "account counts are OBSERVED coverage (a floor), not a licence census; "
                             "chain/independent and alcohol flags are known for `attributed` only"}


def neighborhoods(cbsa, min_accounts=None):
    """Per-ZCTA rows for one metro: resident demand, account count, demand per door, and an index
    versus the metro's own median. Returns rows sorted by demand descending."""
    floor = MIN_ACCOUNTS_FOR_RATIO if min_accounts is None else int(min_accounts)
    geo = _q(GEO, """
        SELECT zcta, COUNT(*) AS accounts, COUNT(DISTINCT source) AS sources
        FROM t WHERE cbsa_code = ? AND zcta IS NOT NULL
        GROUP BY zcta""", [str(cbsa)])
    if not geo:
        return []
    zdem = {r["geo_fips"]: r for r in _q(DEMAND, "SELECT * FROM t WHERE geo_level = 'zcta'")}

    rows = []
    for g in geo:
        d = zdem.get(g["zcta"]) or {}
        dem = float(d.get("demand_total_usd") or 0)
        rows.append({"zcta": g["zcta"], "accounts": g["accounts"], "sources": g["sources"],
                     "households": int(float(d.get("households") or 0)),
                     "demand_total": dem,
                     "demand_at_home": float(d.get("demand_at_home_usd") or 0),
                     "demand_per_hh": float(d.get("demand_per_hh_usd") or 0),
                     "demand_per_account": (dem / g["accounts"]) if (dem and g["accounts"] >= floor) else None,
                     "thin": g["accounts"] < floor})

    # Index against the metro's own MEDIAN demand-per-door, not its mean: a handful of ZIPs with one
    # account and huge demand would drag a mean upward and make every normal neighbourhood look
    # under-served. Same reasoning as ranking price by percentile rather than by % off.
    vals = sorted(r["demand_per_account"] for r in rows if r["demand_per_account"])
    med = vals[len(vals) // 2] if vals else 0
    for r in rows:
        r["index_vs_metro"] = (100.0 * r["demand_per_account"] / med) if (med and r["demand_per_account"]) else None
    rows.sort(key=lambda r: -r["demand_total"])
    return rows


def whitespace(cbsa, top=15):
    """Neighbourhoods with real resident demand and thin coverage — the highest demand-per-door ZIPs,
    excluding ones too thin to rank. This is a COMPARATIVE signal within one metro."""
    rows = [r for r in neighborhoods(cbsa) if r.get("demand_per_account") and r["households"] > 0]
    rows.sort(key=lambda r: -r["demand_per_account"])
    return rows[:top]


def _fmt_usd(v):
    if v >= 1e9:
        return "$%.2fB" % (v / 1e9)
    if v >= 1e6:
        return "$%.1fM" % (v / 1e6)
    if v >= 1e3:
        return "$%.0fk" % (v / 1e3)
    return "$%.0f" % v


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--metro", default=None, help="a CBSA code")
    ap.add_argument("--neighborhoods", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.metro:
        s = metro_summary(a.metro)
        if a.json:
            print(json.dumps(s, indent=1, default=str))
        else:
            print("%s (%s)" % (s["cbsa_name"], s["cbsa_code"]))
            print("  accounts %d across %d ZIPs / %d counties" % (s["accounts"], s["zips"], s["counties"]))
            print("  resident demand %s  (%s on-prem / %s off-prem)" % (
                _fmt_usd(s["demand_total"]), _fmt_usd(s["demand_away"]), _fmt_usd(s["demand_at_home"])))
        if a.neighborhoods:
            print("\n%-8s %8s %10s %12s %8s" % ("ZIP", "ACCTS", "HH", "DEMAND", "IDX"))
            for r in neighborhoods(a.metro)[:40]:
                print("%-8s %8d %10d %12s %8s" % (
                    r["zcta"], r["accounts"], r["households"], _fmt_usd(r["demand_total"]),
                    ("%.0f" % r["index_vs_metro"]) if r["index_vs_metro"] else "-"))
    else:
        print("%-46s %9s %7s %12s %9s" % ("METRO", "ACCOUNTS", "CNTY", "DEMAND/YR", "$/HH"))
        for m in top_metros(a.top):
            print("%-46s %9d %7d %12s %9.0f" % (
                str(m["cbsa_name"])[:46], m["accounts"], m["counties"],
                _fmt_usd(m["demand_total"]), m["demand_per_hh"]))
