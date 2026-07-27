"""fred_ref.py — FRED macro context: the monthly PULSE series the annual layers can't provide.

The economic layer's fast axis ([[economic-data-layer]]): CEX/ACS/EC are annual-or-slower; FRED adds
the monthly read. Scope (each validated live 2026-07, FRED_API_KEY):
  • MRTSSM4453USN — retail sales, beer/wine/liquor stores, $M NSA monthly. The national off-premise
    sales pulse (~$6.3B/mo) — trend + seasonality benchmark for velocity and demand work. (The
    drinking-places twin MRTSSM7224 was discontinued in 2019 — deliberately NOT pulled.)
  • MRTSSM722USN — food services & drinking places total, $M monthly (the on-premise macro trend).
  • DSPIC96 — real disposable personal income (chained $B, SAAR): the demand-side driver.
  • UMCSENT — U. Michigan consumer sentiment: the discretionary-spend mood ring.

Free key required (FRED_API_KEY) — declared in the registry so a keyless host reports no-creds
honestly ([[reference-data-connectors]]). Long/tall fred_reference keyed (series_id, date); a
re-pull refreshes in place and late months simply appear.

    python fred_ref.py            # smoke: series list (no network)
    python fred_ref.py --build    # land fred_reference (needs FRED_API_KEY)
"""
import json, os, sys, time, urllib.request, urllib.parse

API = "https://api.stlouisfed.org/fred/series/observations"

FRED_HEADER = ["dataset", "series_id", "series_name", "date", "vintage_year", "period",
               "metric_value", "unit", "source_pulled_at"]

SERIES = {  # series id -> (friendly name, unit)
    "MRTSSM4453USN": ("liquor_store_sales", "USD_millions_nsa_monthly"),
    "MRTSSM722USN":  ("food_service_sales", "USD_millions_nsa_monthly"),
    "DSPIC96":       ("real_disposable_income", "USD_billions_chained2017_saar"),
    "UMCSENT":       ("consumer_sentiment", "index_1966q1_100"),
}
_START = "2015-01-01"

# Sanity bands per series — a value outside its band means the series changed units/base, and the
# build flags it loudly rather than landing quiet nonsense.
_BANDS = {"MRTSSM4453USN": (2000, 12000), "MRTSSM722USN": (30000, 150000),
          "DSPIC96": (12000, 25000), "UMCSENT": (30, 120)}


def _key():
    return os.environ.get("FRED_API_KEY", "").strip()


def _get(series_id):
    p = {"series_id": series_id, "api_key": _key(), "file_type": "json",
         "observation_start": _START, "sort_order": "asc", "limit": "100000"}
    url = API + "?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+fred reference)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def parse(series_id, payload, ts=None):
    """One FRED observations payload -> long/tall rows. '.' = not published, skipped (never a 0)."""
    ts = ts or int(time.time())
    name, unit = SERIES.get(series_id, (series_id.lower(), ""))
    rows = []
    for o in payload.get("observations", []):
        v = o.get("value", ".")
        if v in (".", "", None):
            continue
        date = o.get("date", "")
        rows.append(dict(zip(FRED_HEADER, [
            "fred", series_id, name, date, int(date[:4]), date[5:7], float(v), unit, ts])))
    return rows


def _check(series_id, rows):
    warns = []
    band = _BANDS.get(series_id)
    if band and rows:
        last = rows[-1]["metric_value"]
        if not (band[0] <= last <= band[1]):
            warns.append("%s: latest %.1f outside sanity band %s — unit/base change? verify before trusting"
                         % (series_id, last, band))
    return warns


def build(log=print):
    """Pull every series -> MERGE into fred_reference. No key / a failed series -> honest degraded."""
    import warehouse
    if not _key():
        log("fred_ref: FRED_API_KEY unset — nothing landed [DEGRADED]")
        return {"rows": 0, "uri": warehouse.uri("fred_reference"),
                "warnings": ["FRED_API_KEY unset"], "degraded": True}
    rows, warns = [], []
    for sid in SERIES:
        try:
            payload = _get(sid)
        except Exception as e:
            warns.append("%s: fetch failed (%s)" % (sid, type(e).__name__)); continue
        if payload.get("error_message"):
            warns.append("%s: %s" % (sid, payload["error_message"][:100])); continue
        r = parse(sid, payload)
        warns += _check(sid, r)
        rows += r
        log("fred %s: %d obs (latest %s)" % (sid, len(r), r[-1]["date"] if r else "—"))
    if not rows:
        log("fred_ref: 0 rows — nothing landed [DEGRADED]")
        return {"rows": 0, "uri": warehouse.uri("fred_reference"), "warnings": warns, "degraded": True}
    res = warehouse.write_accumulate("fred_reference", rows, fields=FRED_HEADER,
                                     key=lambda r: (r["series_id"], r["date"]))
    degraded = bool(warns)
    log("fred_reference: %d rows -> %s%s" % (res["rows"], res["uri"], "  [DEGRADED]" if degraded else ""))
    for w in warns:
        log("  warn: %s" % w)
    return {"rows": res["rows"], "uri": res["uri"], "warnings": warns, "degraded": degraded}


def query(series=None, year=None, limit=5000):
    """Read fred_reference by series id or friendly name, optionally one year."""
    import warehouse
    where, params = ["1=1"], []
    if series:
        where.append("(series_id = ? OR series_name = ?)"); params += [series, series]
    if year not in (None, ""):
        where.append("vintage_year = ?"); params.append(year)
    sql = "SELECT * FROM t WHERE " + " AND ".join(where) + " ORDER BY date LIMIT %d" % int(limit)
    return warehouse.query("fred_reference", sql, params)


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
    else:
        print("FRED series (key %s):" % ("set" if _key() else "NOT SET"))
        for sid, (name, unit) in SERIES.items():
            print("  %-14s %-24s %s" % (sid, name, unit))
