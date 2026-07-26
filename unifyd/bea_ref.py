"""bea_ref.py — BEA regional income: the ANNUAL demand-side driver at county grain.

Why alongside ACS ([[economic-data-layer]]): ACS median income is a 5-year rolling estimate; BEA
personal income is an annual administrative-data figure, fresher and revised on a known schedule —
the cleaner year-over-year demand driver for trade-area work.

Scope (BEA Regional API, free UserID key = BEA_API_KEY):
  • SAINC51 (state) — disposable personal income: line 51 total, 52 population, 53 per-capita ($).
  • CAINC1 (county) — personal income summary: line 1 total ($K), 2 population, 3 per-capita ($).
    (County-grain DISPOSABLE income is not published; per-capita personal income is the county driver.)

KEY-ACTIVATION GOTCHA (hit live 2026-07): a fresh BEA UserID returns HTTP 200 with
Results.Error APIErrorCode 4 "This UserId is not active" until the emailed activation link is
clicked. build() detects the in-band Error object and reports DEGRADED with the exact message —
never a silent empty land. The data-path parse follows BEA's documented Data[] shape and is
confirmed on the first active-key run.

    python bea_ref.py            # smoke: the exact calls (no network)
    python bea_ref.py --build    # land bea_reference (needs an ACTIVATED BEA_API_KEY)
"""
import json, os, sys, time, urllib.request, urllib.parse

API = "https://apps.bea.gov/api/data"

BEA_HEADER = ["dataset", "table_name", "line_code", "metric_name", "geo_level", "geo_fips",
              "vintage_year", "metric_value", "unit", "source_pulled_at"]

# (table, line, geo) -> metric name + unit. BEA GeoFips: states are '<ss>000', counties '<ss><ccc>'.
# LINE CODES ARE PER-TABLE, not positional (validated via GetParameterValuesFiltered): SAINC51 uses
# 51/52/53, CAINC1 uses 1/2/3 — a wrong code returns top-level Error 40, not an empty result set.
PULLS = [
    ("SAINC51", "51", "STATE",  "disposable_income_total",      "USD_millions"),
    ("SAINC51", "52", "STATE",  "population",                   "persons"),
    ("SAINC51", "53", "STATE",  "disposable_income_per_capita", "USD"),
    ("CAINC1",  "1",  "COUNTY", "personal_income_total",        "USD_thousands"),
    ("CAINC1",  "2",  "COUNTY", "population",                   "persons"),
    ("CAINC1",  "3",  "COUNTY", "personal_income_per_capita",   "USD"),
]


def _key():
    return os.environ.get("BEA_API_KEY", "").strip()


def url_for(table, line, geo, year):
    p = {"UserID": _key() or "…", "method": "GetData", "datasetname": "Regional",
         "TableName": table, "LineCode": line, "GeoFips": geo, "Year": str(year), "ResultFormat": "JSON"}
    return API + "?" + urllib.parse.urlencode(p)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+bea reference)"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None                      # BEA marks suppressed/NA cells with letters — never a 0


def parse(payload, table, line, geo_level, metric, unit, ts=None):
    """One GetData payload -> rows. Returns (rows, error) — error carries BEA's in-band Error text
    (e.g. the not-yet-activated key) so build() can degrade with the real reason."""
    ts = ts or int(time.time())
    api = payload.get("BEAAPI") or {}
    res = api.get("Results") or {}
    # BEA reports errors at TWO levels: Results.Error (e.g. inactive key) and top-level BEAAPI.Error
    # (e.g. invalid LineCode) — missing the second silently yields 0 rows (hit live 2026-07).
    err = res.get("Error") or api.get("Error")
    if err:
        detail = (err.get("ErrorDetail") or {}).get("Description", "")
        return [], "%s%s (code %s)" % (err.get("APIErrorDescription", "?"),
                                       " — " + detail.strip() if detail else "", err.get("APIErrorCode", "?"))
    rows = []
    for d in res.get("Data") or []:
        fips = str(d.get("GeoFips", ""))
        if geo_level == "STATE":
            if not fips.endswith("000"):
                continue
            fips = fips[:2]
        val = _num(d.get("DataValue"))
        try:
            yr = int(str(d.get("TimePeriod", ""))[:4])
        except ValueError:
            continue
        rows.append(dict(zip(BEA_HEADER, ["bea", table, line, metric, geo_level.lower(), fips,
                                          yr, val, unit, ts])))
    return rows, None


def build(years=None, log=print):
    """Land state DPI + county personal income for the year range. Inactive/absent key -> honest
    degraded with BEA's own message; a partial pull merges what it got (cell-replace accumulate)."""
    import warehouse
    if not _key():
        log("bea_ref: BEA_API_KEY unset — nothing landed [DEGRADED]")
        return {"rows": 0, "uri": warehouse.uri("bea_reference"),
                "warnings": ["BEA_API_KEY unset"], "degraded": True}
    years = years or list(range(2015, time.localtime().tm_year))
    rows, warns, ts = [], [], int(time.time())
    for table, line, geo, metric, unit in PULLS:
        try:
            payload = _get(url_for(table, line, geo, ",".join(str(y) for y in years)))
        except Exception as e:
            warns.append("%s L%s %s: fetch failed (%s)" % (table, line, geo, type(e).__name__)); continue
        r, err = parse(payload, table, line, geo, metric, unit, ts)
        if err:
            warns.append("%s L%s: %s" % (table, line, err))
            if "not active" in err:
                warns.append("BEA key exists but is NOT ACTIVATED — click the link in BEA's signup email, then re-run")
                break                      # every further call fails identically; fail fast + loud
            continue
        if not r:
            warns.append("%s L%s %s: 0 cells with no error object — response shape drift, verify" % (table, line, geo))
            continue
        rows += r
        log("bea %s L%s %s: %d cells" % (table, line, geo, len(r)))
    if not rows:
        log("bea_ref: 0 rows — nothing landed [DEGRADED]")
        return {"rows": 0, "uri": warehouse.uri("bea_reference"), "warnings": warns, "degraded": True}
    res = warehouse.write_accumulate(
        "bea_reference", rows, fields=BEA_HEADER,
        key=lambda r: (r["dataset"], r["table_name"], r["line_code"], r["geo_level"],
                       r["geo_fips"], r["vintage_year"]))
    degraded = bool(warns)
    log("bea_reference: %d rows -> %s%s" % (res["rows"], res["uri"], "  [DEGRADED]" if degraded else ""))
    for w in warns:
        log("  warn: %s" % w)
    return {"rows": res["rows"], "uri": res["uri"], "warnings": warns, "degraded": degraded}


def query(metric=None, geo_level=None, geo_fips=None, year=None, limit=5000):
    import warehouse
    where, params = ["1=1"], []
    for col, val in (("metric_name", metric), ("geo_level", geo_level),
                     ("geo_fips", geo_fips), ("vintage_year", year)):
        if val not in (None, ""):
            where.append("%s = ?" % col); params.append(val)
    sql = "SELECT * FROM t WHERE " + " AND ".join(where) + " ORDER BY vintage_year LIMIT %d" % int(limit)
    return warehouse.query("bea_reference", sql, params)


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
    else:
        print("BEA Regional pulls (key %s):" % ("set" if _key() else "NOT SET"))
        for table, line, geo, metric, unit in PULLS:
            print("  %s L%s %-7s %-28s %s" % (table, line, geo, metric, unit))
        print("\nexample call:\n  " + url_for("SAINC51", "3", "STATE", 2023).replace(_key() or "…", "KEY"))
