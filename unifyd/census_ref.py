"""census_ref.py — U.S. Census reference layer: CBP / Nonemployer / PEP (supply-side) + ACS (demand-side).

A REFERENCE/dimension layer for the MDM: aggregate counts by geography + NAICS, joined to entity
tables (permits, products, retailers, territories) at QUERY TIME by geo/NAICS — never a baked FK.
Long/tall `census_reference` so any dataset/metric fits without a schema change. Alcohol scope:
NAICS 4248 (bev-alc merchant wholesalers), 44531 (beer/wine/liquor stores), 722 (food service &
drinking places, for on-premise). Free Census API — no scrape — but now needs CENSUS_API_KEY for
ALL requests (keyless -> 302 missing_key). Stores to the warehouse (Parquet/DuckDB).

Parses by HEADER NAME (array-of-arrays, header row first) so it survives Census's column reshuffles.
build() runs the four datasets → long/tall rows; query() reads them back by dataset/geo/naics/metric.
Suppression (cells Census withholds for confidentiality) is a flagged state, never treated as 0.
"""
import os, json, time, urllib.request, urllib.parse

NAICS = ["4248", "44531", "722"]
# Vintages validated LIVE on Fly (Census now requires a key for all requests):
#   CBP 2022 ✓ · Nonemployer 2019 ✓ (var NESTAB, not ESTAB) · PEP 2019 ✓ (newer PEP vintages not in
#   the API) · SUSB — NO API (not in data.json; bulk download only) → dropped from the live layer.
CBP_YEAR, NONEMP_YEAR, PEP_YEAR = 2022, 2019, 2019
REF_HEADER = ["dataset", "vintage_year", "naics_code", "geo_level", "geo_fips",
              "metric_name", "metric_value", "suppressed", "source_pulled_at"]


def _key():
    return os.environ.get("CENSUS_API_KEY", "").strip()


def _get(path, params):
    p = dict(params)
    if _key():
        p["key"] = _key()
    url = "https://api.census.gov/data/%s?%s" % (path, urllib.parse.urlencode(p))
    req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+census reference)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read().decode("utf-8", "replace")
    if not body.strip() or body.lstrip().startswith("<"):
        raise RuntimeError("non-JSON (missing key?) from %s" % path)
    return json.loads(body)


def _rows(data):
    if not data or len(data) < 2:
        return []
    hdr = data[0]
    return [dict(zip(hdr, row)) for row in data[1:]]


def _num(v):
    try:
        f = float(v)
        return f if f > -1e8 else None          # Census jam/suppression sentinels are large negatives
    except (TypeError, ValueError):
        return None


def _emit(out, dataset, year, naics, geo_level, fips, metrics, row, flags, ts):
    """Append one long/tall row per metric; suppression from the metric's _F flag or a null value.
    Unparseable/suppressed values land as None (not "") so metric_value stays a typed float column —
    a mixed float/str column fails pyarrow inference when merged with prior typed rows."""
    for m in metrics:
        raw = row.get(m)
        val = _num(raw)
        flag = (row.get(m + "_F") or "").strip() if flags else ""
        supp = bool(flag) or (val is None and raw not in ("0", 0))
        out.append([dataset, year, naics, geo_level, fips, m.lower(), val, supp, ts])


def pull_cbp(year=CBP_YEAR, naics=NAICS):
    """County Business Patterns — establishment count / employment / payroll by NAICS, county+state."""
    out, ts = [], int(time.time())
    metrics = ["ESTAB", "EMP", "PAYANN", "PAYQTR1"]
    get = ",".join(metrics + ["EMP_F", "PAYANN_F"])
    for n in naics:
        for level, params in (("county", {"for": "county:*", "in": "state:*"}),
                              ("state", {"for": "state:*"})):
            try:
                rows = _rows(_get("%d/cbp" % year, dict(params, **{"get": get, "NAICS2017": n})))
            except Exception:
                continue
            for r in rows:
                fips = r.get("state", "") + r.get("county", "") if level == "county" else r.get("state", "")
                _emit(out, "cbp", year, n, level, fips, metrics, r, True, ts)
    return out


def pull_nonemp(year=NONEMP_YEAR, naics=NAICS):
    """Nonemployer Statistics — no-paid-employee businesses (NESTAB) + receipts (NRCPTOT), by NAICS,
    state-level. Complements CBP's employer-only counts (important for small/independent operators)."""
    out, ts = [], int(time.time())
    for n in naics:
        try:
            rows = _rows(_get("%d/nonemp" % year, {"get": "NESTAB,NRCPTOT", "for": "state:*", "NAICS2017": n}))
        except Exception:
            continue
        for r in rows:
            _emit(out, "nonemployer", year, n, "state", r.get("state", ""), ["NESTAB", "NRCPTOT"], r, False, ts)
    return out


# SUSB (Statistics of U.S. Businesses, by enterprise size) has NO Census API — bulk download only.
# Phase 2 if chain-vs-independent segmentation is needed: ingest the SUSB flat file separately.


def pull_pep(year=PEP_YEAR):
    """Population Estimates — county + state population (per-capita denominator). Latest API vintage is
    2019 (newer PEP vintages aren't exposed via the API). Not NAICS-scoped."""
    out, ts = [], int(time.time())
    for level, params in (("county", {"for": "county:*", "in": "state:*"}), ("state", {"for": "state:*"})):
        try:
            rows = _rows(_get("%d/pep/population" % year, dict(params, **{"get": "POP,NAME"})))
        except Exception:
            continue
        for r in rows:
            fips = r.get("state", "") + r.get("county", "") if level == "county" else r.get("state", "")
            out.append(["pep", year, "", level, fips, "population", _num(r.get("POP")), False, ts])
    return out


ACS_YEAR = 2022
# ACS 5-year DEMAND-side variables: median HH income, median age, households, total population, and the
# income-distribution brackets used to derive the $100k+ household share (B19001_014E..017E / _001E).
ACS_VARS = ["B19013_001E", "B01002_001E", "B11001_001E", "B01003_001E",
            "B19001_001E", "B19001_014E", "B19001_015E", "B19001_016E", "B19001_017E"]


def pull_acs(year=ACS_YEAR):
    """ACS 5-year consumer demographics (median household income, median age, households, population,
    $100k+ household count) — county + state. The DEMAND-side complement to CBP/PEP: trade-area
    enrichment an account/geo subject reads by geo_fips. Emits friendly metric names under dataset 'acs'."""
    out, ts = [], int(time.time())
    get = ",".join(["NAME"] + ACS_VARS)
    for level, params in (("county", {"for": "county:*", "in": "state:*"}), ("state", {"for": "state:*"})):
        try:
            rows = _rows(_get("%d/acs/acs5" % year, dict(params, **{"get": get})))
        except Exception:
            continue
        for r in rows:
            fips = r.get("state", "") + r.get("county", "") if level == "county" else r.get("state", "")
            base = _num(r.get("B19001_001E"))
            brackets = [_num(r.get("B19001_%03dE" % b)) for b in (14, 15, 16, 17)]
            hi = sum(v for v in brackets if v is not None) if base else None
            cells = [("median_hh_income", _num(r.get("B19013_001E"))),
                     ("median_age",       _num(r.get("B01002_001E"))),
                     ("households",       _num(r.get("B11001_001E"))),
                     ("population",       _num(r.get("B01003_001E"))),
                     ("hh_income_base",   base),
                     ("hh_100k_plus",     hi)]
            for metric, val in cells:
                out.append(["acs", year, "", level, fips, metric, val, val is None, ts])
    return out


def _cell_key(r):
    """The identity a re-pull REPLACES: one (dataset, vintage, naics, geo, metric) cell."""
    return (r["dataset"], r["vintage_year"], r["naics_code"], r["geo_level"], r["geo_fips"], r["metric_name"])


def build(log=print):
    """Run the datasets → long/tall census_reference; MERGE into the warehouse. Returns stats.

    Persistent catalog, so write_accumulate (never write_parquet): a re-pull replaces each
    (dataset, vintage, naics, geo, metric) cell it actually fetched and keeps everything else,
    so a partial pull (one dataset erroring, per-request excepts swallowed above) can no longer
    shrink the table — this is the writer that wiped census_reference 24,546 → 0 when a keyless
    pull returned nothing and landed as truth. An all-empty pull now raises instead of writing."""
    import warehouse
    rows = []
    for name, fn in (("cbp", pull_cbp), ("nonemployer", pull_nonemp), ("pep", pull_pep), ("acs", pull_acs)):
        r = fn()
        log("census %s: %d rows" % (name, len(r)))
        rows.extend(r)
    if not rows:
        raise RuntimeError("census build: all pulls returned 0 rows (missing/invalid CENSUS_API_KEY "
                           "or API down) — refusing to land an empty census_reference")
    res = warehouse.write_accumulate("census_reference", [dict(zip(REF_HEADER, r)) for r in rows],
                                     key=_cell_key, fields=REF_HEADER)
    log("census_reference: %d rows total -> %s" % (res["rows"], res["uri"]))
    return {"rows": res["rows"], "uri": res["uri"]}


def query(dataset=None, geo_level=None, geo_fips=None, naics=None, metric=None, vintage=None, limit=5000):
    """Read census_reference by any combination (the territory.html join helper). Non-suppressed only
    unless you ask for a specific cell. All matching handled in DuckDB against the Parquet."""
    import warehouse
    where, params = ["1=1"], []
    for col, val in (("dataset", dataset), ("geo_level", geo_level), ("geo_fips", geo_fips),
                     ("naics_code", naics), ("vintage_year", vintage)):
        if val not in (None, ""):
            where.append("%s = ?" % col); params.append(val)
    if metric:
        where.append("metric_name = ?"); params.append(metric)
    sql = "SELECT * FROM t WHERE " + " AND ".join(where) + " LIMIT %d" % int(limit)
    return warehouse.query("census_reference", sql, params)


if __name__ == "__main__":
    # requires CENSUS_API_KEY in env; prints a small CBP sample
    os.environ.setdefault("CENSUS_API_KEY", "")
    if not _key():
        print("set CENSUS_API_KEY to test"); raise SystemExit
    print("cbp sample:", pull_cbp()[:3])
