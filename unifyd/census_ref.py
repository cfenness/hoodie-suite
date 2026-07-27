"""census_ref.py — U.S. Census reference layer: CBP / Nonemployer / PEP (supply-side) + ACS (demand-side).

A REFERENCE/dimension layer for the MDM: aggregate counts by geography + NAICS, joined to entity
tables (permits, products, retailers, territories) at QUERY TIME by geo/NAICS — never a baked FK.
Long/tall `census_reference` so any dataset/metric fits without a schema change. Alcohol scope:
NAICS 4248 (bev-alc merchant wholesalers), 44531 (beer/wine/liquor stores), 722 (food service &
drinking places, for on-premise). Free Census API — no scrape — but now needs CENSUS_API_KEY for
ALL requests (keyless -> 302 missing_key). Stores to the warehouse (Parquet/DuckDB).

Parses by HEADER NAME (array-of-arrays, header row first) so it survives Census's column reshuffles.
build() runs CBP/Nonemployer/PEP/ACS(featured)/Economic-Census → long/tall census_reference rows;
query() reads them back by dataset/geo/naics/metric. build_acs() separately sweeps ALL ~1,193 ACS5
detailed tables into census_acs; build_flows() lands county-to-county migration into census_migration.
Suppression (cells Census withholds for confidentiality) is a flagged state, never treated as 0.
"""
import os, re, sys, json, time, urllib.request, urllib.parse

NAICS = ["4248", "44531", "722"]
# Vintages validated LIVE on Fly (Census now requires a key for all requests):
#   CBP 2022 ✓ · Nonemployer 2019 ✓ (var NESTAB, not ESTAB) · PEP 2019 ✓ (newer PEP vintages not in
#   the API) · SUSB — NO API (not in data.json; bulk download only) → dropped from the live layer.
CBP_YEAR, NONEMP_YEAR, PEP_YEAR = 2022, 2019, 2019
REF_HEADER = ["dataset", "vintage_year", "naics_code", "geo_level", "geo_fips",
              "metric_name", "metric_value", "suppressed", "source_pulled_at"]

# ── Full ACS5 sweep (market-size breadth) / migration-momentum layers (keyless-verified 2026-07) ──────────
# ACS5-full adds the complete detailed-table breadth the narrower `acs` pull below doesn't reach; Flows
# adds migration momentum. Economic Census (the SALES $ axis) is already covered by pull_ecn above
# (dataset 'ecn') — not duplicated here. ACS5-full + Flows are big/wide enough to want their OWN tables
# (census_acs / census_migration), separate from census_reference's long/tall shape. Both need
# CENSUS_API_KEY — validate LIVE on Fly like the rest.
ACS5_YEAR, FLOWS_YEAR = 2023, 2022
FLOW_METRICS = ["MOVEDIN", "MOVEDOUT", "MOVEDNET", "FROMABROAD"]
# Featured ACS estimates promoted for the common bev-alc questions (landed at COUNTY grain alongside the
# full state-level all-tables sweep). code -> friendly metric name.
ACS_FEATURED = {
    "B01003_001E": "total_population", "B19013_001E": "median_household_income",
    "B19301_001E": "per_capita_income", "B19025_001E": "aggregate_household_income",
    "B11001_001E": "total_households", "B25001_001E": "housing_units",
    "B03002_001E": "race_ethnicity_universe", "B03002_012E": "hispanic_or_latino",
}
ACS_HEADER = ["dataset", "vintage_year", "table_id", "variable", "label",
              "geo_level", "geo_fips", "estimate", "suppressed", "source_pulled_at"]
FLOW_HEADER = ["vintage_year", "geo_fips", "geo_name", "other_fips", "other_name",
               "metric_name", "metric_value", "source_pulled_at"]
_EST_RE = re.compile(r"_\d+E$")           # ACS estimate columns (…_001E), not margins/annotations


def _key():
    return os.environ.get("CENSUS_API_KEY", "").strip()


def _get(path, params, timeout=120):
    p = dict(params)
    if _key():
        p["key"] = _key()
    url = "https://api.census.gov/data/%s?%s" % (path, urllib.parse.urlencode(p))
    req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+census reference)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
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


ECN_YEAR = 2022
# Economic Census NAICS-2022 scope (the 2022 recode renamed 44531 -> 44532 Beer/Wine/Liquor RETAILERS):
# retailers + wholesalers + food service total + drinking places + full-service restaurants.
ECN_NAICS = ["44532", "4248", "722", "722410", "722511"]


def pull_ecn(year=ECN_YEAR, naics=ECN_NAICS):
    """Economic Census — OBSERVED receipts (the market-size denominator the modeled CEX×ACS demand is
    checked against). RCPTOT/PAYANN are in $1,000s (Census convention — same as the govs AMOUNT gotcha
    in tax_revenue.py); ESTAB/EMP are counts. Dataset 'ecn', county + state, every-5-years vintage
    (2022 EC published 2024–26). Observed receipts include VISITOR spend — the resident-modeled vs
    observed gap is signal (tourism + CEX underreport), not error."""
    out, ts = [], int(time.time())
    metrics = ["ESTAB", "EMP", "PAYANN", "RCPTOT"]
    for n in naics:
        for level, params in (("county", {"for": "county:*", "in": "state:*"}), ("state", {"for": "state:*"})):
            try:
                rows = _rows(_get("%d/ecnbasic" % year, dict(params, **{"get": ",".join(metrics), "NAICS2022": n})))
            except Exception:
                continue
            for r in rows:
                fips = r.get("state", "") + r.get("county", "") if level == "county" else r.get("state", "")
                _emit(out, "ecn", year, n, level, fips, metrics, r, False, ts)
    return out


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
# The FULL ACS B19001 household-income distribution (16 brackets), landed under stable metric names so
# cex_ref.py can join CEX mean-spend-by-income onto it for trade-area demand $ (ACS_TO_CEX keys on these).
ACS_INC = [("hh_lt10k", "B19001_002E"), ("hh_10_15k", "B19001_003E"), ("hh_15_20k", "B19001_004E"),
           ("hh_20_25k", "B19001_005E"), ("hh_25_30k", "B19001_006E"), ("hh_30_35k", "B19001_007E"),
           ("hh_35_40k", "B19001_008E"), ("hh_40_45k", "B19001_009E"), ("hh_45_50k", "B19001_010E"),
           ("hh_50_60k", "B19001_011E"), ("hh_60_75k", "B19001_012E"), ("hh_75_100k", "B19001_013E"),
           ("hh_100_125k", "B19001_014E"), ("hh_125_150k", "B19001_015E"),
           ("hh_150_200k", "B19001_016E"), ("hh_200k_plus", "B19001_017E")]
# ACS 5-year DEMAND-side variables: median HH income, median age, households, total population, and the
# income-distribution brackets (full B19001 set; _014E..017E also derive the $100k+ household share).
ACS_VARS = ["B19013_001E", "B01002_001E", "B11001_001E", "B01003_001E",
            "B19001_001E"] + [v for _, v in ACS_INC]


def pull_acs(year=ACS_YEAR):
    """ACS 5-year consumer demographics (median household income, median age, households, population,
    $100k+ household count) — state + county + ZCTA (~33k ZIPs, the grain that separates Baldwin Park
    from Pine Hills). The DEMAND-side complement to CBP/PEP: trade-area enrichment an account/geo
    subject reads by geo_fips (ZCTA rows key on the 5-digit ZIP). Emits friendly metric names under
    dataset 'acs'. ZCTAs are national in the 2020+ vintages (no state hierarchy needed); tiny ZCTAs
    legitimately land suppressed medians — flagged, never zeroed."""
    out, ts = [], int(time.time())
    get = ",".join(["NAME"] + ACS_VARS)
    for level, params in (("county", {"for": "county:*", "in": "state:*"}), ("state", {"for": "state:*"}),
                          ("zcta", {"for": "zip code tabulation area:*"})):
        try:
            rows = _rows(_get("%d/acs/acs5" % year, dict(params, **{"get": get}),
                              timeout=600 if level == "zcta" else 120))   # national ZCTA response is ~10MB
        except Exception:
            continue
        for r in rows:
            if level == "zcta":
                fips = r.get("zip code tabulation area", "")
            else:
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
            cells += [(name, _num(r.get(var))) for name, var in ACS_INC]
            for metric, val in cells:
                out.append(["acs", year, "", level, fips, metric, val, val is None, ts])
    return out


# ── ACS 5-year FULL SWEEP (all ~1,193 detailed tables @ state + featured @ county) + migration flows ──────
# Genuinely new breadth vs pull_acs above (which lands ~21 featured variables into census_reference):
# pull_acs5 sweeps every B/C detailed table and lands into its own census_acs table; pull_flows lands
# county-to-county migration momentum into census_migration. (Economic Census / market-SIZE $ is already
# covered by pull_ecn above — dataset 'ecn' — so it is NOT re-added here.)
def _get_json(url):
    """Fetch Census metadata JSON (variables/groups) — keyless, parsed by header name so it survives
    Census's periodic reshuffles."""
    req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+census reference)"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


_ACS_LABEL_CACHE = {}
def _acs_labels(year):
    """{estimate_code: label} across ACS5 detailed tables — fetched once per vintage."""
    if year in _ACS_LABEL_CACHE:
        return _ACS_LABEL_CACHE[year]
    m = {}
    try:
        v = _get_json("https://api.census.gov/data/%d/acs/acs5/variables.json" % year).get("variables", {})
        for code, meta in v.items():
            if _EST_RE.search(code):
                m[code] = (meta.get("label", "") or "").replace("!!", " ").strip()
    except Exception:
        pass
    _ACS_LABEL_CACHE[year] = m
    return m


def _acs_groups(year):
    """All ACS5 detailed-table ids (B/C tables) — the 'all ACS' universe (~1,193)."""
    try:
        g = _get_json("https://api.census.gov/data/%d/acs/acs5/groups.json" % year).get("groups", [])
        return sorted(x["name"] for x in g if x.get("name", "")[:1] in ("B", "C"))
    except Exception:
        return []


def _acs_21plus_code(year):
    """Resolve the DP05 '21 years and over' estimate code by LABEL — profile line numbers shift by vintage,
    so we never hardcode DP05_00xxE."""
    try:
        v = _get_json("https://api.census.gov/data/%d/acs/acs5/profile/groups/DP05.json" % year).get("variables", {})
        for code, meta in sorted(v.items()):
            if _EST_RE.search(code) and (meta.get("label", "") or "").rstrip("!").endswith("21 years and over"):
                return code
    except Exception:
        pass
    return None


def pull_acs5(year=ACS5_YEAR, log=print):
    """ALL ~1,193 ACS5 detailed tables at STATE grain (full breadth, ~1.2M cells — bounded so the single
    in-memory write can't OOM) + the FEATURED bev-alc estimates at COUNTY grain (the granular signals).
    One group() call per table. Long/tall into census_acs with code + human label. (Full all-tables×county
    and tract/block-group is a deliberate PARTITIONED/bulk follow-up — 78M+ cells, not a one-shot write.)"""
    labels, ts, out = _acs_labels(year), int(time.time()), []
    groups = _acs_groups(year)
    for i, g in enumerate(groups):                                   # all detailed tables @ state
        try:
            data = _get("%d/acs/acs5" % year, {"get": "group(%s)" % g, "for": "state:*"})
        except Exception:
            continue
        hdr = data[0]; est = [c for c in hdr if _EST_RE.search(c)]
        for row in data[1:]:
            r = dict(zip(hdr, row)); fips = r.get("state", "")
            for c in est:
                val = _num(r.get(c))
                out.append(["acs5", year, g, c, labels.get(c, ""), "state", fips, val, val is None, ts])
        if i % 250 == 0:
            log("census acs5 state: %d/%d tables (%d rows)" % (i, len(groups), len(out)))
        time.sleep(0.03)                                             # politeness
    feat = list(ACS_FEATURED)                                       # featured @ county (chunked <=45/call)
    for j in range(0, len(feat), 45):
        chunk = feat[j:j + 45]
        try:
            data = _get("%d/acs/acs5" % year, {"get": ",".join(chunk), "for": "county:*", "in": "state:*"})
        except Exception:
            continue
        hdr = data[0]
        for row in data[1:]:
            r = dict(zip(hdr, row)); fips = r.get("state", "") + r.get("county", "")
            for c in chunk:
                val = _num(r.get(c))
                out.append(["acs5", year, c.split("_")[0], c, ACS_FEATURED[c], "county", fips, val, val is None, ts])
    tp = _acs_21plus_code(year)                                     # 21+ (legal drinking age) @ county
    if tp:
        try:
            data = _get("%d/acs/acs5/profile" % year, {"get": tp, "for": "county:*", "in": "state:*"})
            hdr = data[0]
            for row in data[1:]:
                r = dict(zip(hdr, row)); fips = r.get("state", "") + r.get("county", "")
                val = _num(r.get(tp))
                out.append(["acs5", year, "DP05", tp, "population_21_plus", "county", fips, val, val is None, ts])
        except Exception:
            log("census acs5: 21+ profile pull failed (kept everything else)")
    log("census acs5: %d rows (%d tables @ state + featured @ county)" % (len(out), len(groups)))
    return out


def pull_flows(year=FLOWS_YEAR, log=print):
    """ACS county-to-county Migration Flows — MOVEDIN/MOVEDOUT/MOVEDNET (+ FROMABROAD) per county-pair. The
    market-MOMENTUM signal (which trade areas are growing vs shrinking) that a static snapshot misses."""
    out, ts = [], int(time.time())
    get = ",".join(FLOW_METRICS + ["GEOID1", "GEOID2", "FULL1_NAME", "FULL2_NAME"])
    for st in _FLOW_STATES:
        try:
            rows = _rows(_get("%d/acs/flows" % year, {"get": get, "for": "county:*", "in": "state:%s" % st}))
        except Exception:
            continue
        for r in rows:
            f1, f2 = r.get("GEOID1", ""), r.get("GEOID2", "")
            for m in FLOW_METRICS:
                val = _num(r.get(m))
                if val is None:
                    continue
                out.append([year, f1, r.get("FULL1_NAME", ""), f2, r.get("FULL2_NAME", ""), m.lower(), val, ts])
    log("census flows: %d county-pair rows" % len(out))
    return out


_FLOW_STATES = ["%02d" % s for s in (1, 2, 4, 5, 6, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20, 21, 22, 23,
                24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 44, 45, 46, 47,
                48, 49, 50, 51, 53, 54, 55, 56)]


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
    for name, fn in (("cbp", pull_cbp), ("nonemployer", pull_nonemp), ("pep", pull_pep), ("acs", pull_acs),
                     ("ecn", pull_ecn)):
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


def build_acs(log=print):
    """ALL ACS5 detailed tables @ state + featured bev-alc estimates @ county → census_acs (full snapshot,
    write_parquet — the empty-write guard refuses to clobber a good table with a keyless 0-row pull)."""
    import warehouse
    rows = pull_acs5(log=log)
    if not rows:
        raise RuntimeError("census acs5: 0 rows (missing CENSUS_API_KEY or API down) — refusing empty write")
    res = warehouse.write_parquet("census_acs", [dict(zip(ACS_HEADER, r)) for r in rows], fields=ACS_HEADER)
    log("census_acs: %d rows -> %s" % (res["rows"], res["uri"]))
    return {"rows": res["rows"], "uri": res["uri"]}


def build_flows(log=print):
    """ACS county-to-county migration flows → census_migration (full snapshot)."""
    import warehouse
    rows = pull_flows(log=log)
    if not rows:
        raise RuntimeError("census flows: 0 rows (missing CENSUS_API_KEY or API down) — refusing empty write")
    res = warehouse.write_parquet("census_migration", [dict(zip(FLOW_HEADER, r)) for r in rows], fields=FLOW_HEADER)
    log("census_migration: %d rows -> %s" % (res["rows"], res["uri"]))
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
    # `--plan` dry-runs the metadata (keyless): ACS5-full table universe + resolved 21+ code + flows scope.
    # Without --plan, needs CENSUS_API_KEY and prints a small CBP sample.
    if "--plan" in sys.argv:
        y = ACS5_YEAR
        groups = _acs_groups(y)
        print("ACS5 %d: %d detailed tables (all landed @ state) + %d featured @ county" % (y, len(groups), len(ACS_FEATURED)))
        print("  21+ code resolved by label:", _acs_21plus_code(y))
        print("  est. rows ~= %d tables x 52 states x ~20 vars + featured x 3220 counties" % len(groups))
        print("Flows %d: %d states x county-pairs x %s -> census_migration" % (FLOWS_YEAR, len(_FLOW_STATES), FLOW_METRICS))
        raise SystemExit
    os.environ.setdefault("CENSUS_API_KEY", "")
    if not _key():
        print("set CENSUS_API_KEY to test (or run with --plan for a keyless dry-run)"); raise SystemExit
    print("cbp sample:", pull_cbp()[:3])
