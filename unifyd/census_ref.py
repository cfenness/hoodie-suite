"""census_ref.py — U.S. Census supply-side reference layer (CBP / Nonemployer / SUSB / PEP).

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
import os, re, sys, json, time, urllib.request, urllib.parse

NAICS = ["4248", "44531", "722"]
# Vintages validated LIVE on Fly (Census now requires a key for all requests):
#   CBP 2022 ✓ · Nonemployer 2019 ✓ (var NESTAB, not ESTAB) · PEP 2019 ✓ (newer PEP vintages not in
#   the API) · SUSB — NO API (not in data.json; bulk download only) → dropped from the live layer.
CBP_YEAR, NONEMP_YEAR, PEP_YEAR = 2022, 2019, 2019
REF_HEADER = ["dataset", "vintage_year", "naics_code", "geo_level", "geo_fips",
              "metric_name", "metric_value", "suppressed", "source_pulled_at"]

# ── Market-size / demographics / momentum layers (keyless-verified 2026-07) ──────────────────────────────
# Economic Census adds the SALES ($) axis CBP lacks; ACS adds trade-area demographics; Flows adds
# migration momentum. EC folds into census_reference (same long/tall shape, NAICS-scoped); ACS + Flows get
# their own tables (different shape / volume). All need CENSUS_API_KEY — validate LIVE on Fly like the rest.
EC_YEAR, ACS5_YEAR, FLOWS_YEAR = 2022, 2023, 2022
# Economic Census bev-alc NAICS (2022): off-prem retail, bev-alc wholesale, on-prem drinking places. The
# connector skips any code the API doesn't carry, so listing both 4- and 6-digit is safe.
EC_NAICS = ["4453", "445320", "4248", "424810", "424820", "7224", "722410"]
EC_METRICS = ["ESTAB", "RCPTOT", "PAYANN", "EMP"]          # RCPTOT = sales/receipts ($1,000s); ESTAB/EMP = counts
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


def pull_ecn(year=EC_YEAR, naics=EC_NAICS, log=print):
    """Economic Census (ecnbasic) — establishments + SALES/receipts (RCPTOT) + payroll + employment by
    NAICS, at US/state/county. The market-SIZE ($) axis CBP lacks. Lands into census_reference (same
    long/tall shape); dataset='ecnbasic'. RCPTOT/PAYANN are $1,000s; ESTAB/EMP are counts. Suppressed
    cells (Census disclosure) land as None, never 0."""
    out, ts = [], int(time.time())
    get = ",".join(EC_METRICS)
    for n in naics:
        for level, params in (("us", {"for": "us:*"}), ("state", {"for": "state:*"}),
                              ("county", {"for": "county:*", "in": "state:*"})):
            try:
                rows = _rows(_get("%d/ecnbasic" % year, dict(params, **{"get": get, "NAICS2022": n})))
            except Exception:
                continue
            for r in rows:
                fips = (r.get("state", "") + r.get("county", "")) if level == "county" \
                    else (r.get("state", "") if level == "state" else "US")
                _emit(out, "ecnbasic", year, n, level, fips, EC_METRICS, r, False, ts)
    log("census ecnbasic: %d rows (%d NAICS)" % (len(out), len(naics)))
    return out


# ── ACS 5-year (all detailed tables @ state + featured @ county) ─────────────────────────────────────────
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
    for name, fn in (("cbp", pull_cbp), ("nonemployer", pull_nonemp), ("pep", pull_pep), ("ecnbasic", pull_ecn)):
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
    # `--plan` dry-runs the metadata (keyless): ACS table universe + resolved 21+ code + EC/flows scope.
    # Without --plan, needs CENSUS_API_KEY and prints a small CBP sample.
    if "--plan" in sys.argv:
        y = ACS5_YEAR
        groups = _acs_groups(y)
        print("ACS5 %d: %d detailed tables (all landed @ state) + %d featured @ county" % (y, len(groups), len(ACS_FEATURED)))
        print("  21+ code resolved by label:", _acs_21plus_code(y))
        print("  est. rows ~= %d tables x 52 states x ~20 vars + featured x 3220 counties" % len(groups))
        print("Economic Census %d: NAICS %s x {us,state,county} x %s -> census_reference" % (EC_YEAR, EC_NAICS, EC_METRICS))
        print("Flows %d: %d states x county-pairs x %s -> census_migration" % (FLOWS_YEAR, len(_FLOW_STATES), FLOW_METRICS))
        raise SystemExit
    os.environ.setdefault("CENSUS_API_KEY", "")
    if not _key():
        print("set CENSUS_API_KEY to test (or run with --plan for a keyless dry-run)"); raise SystemExit
    print("cbp sample:", pull_cbp()[:3])
