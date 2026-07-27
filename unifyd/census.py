"""census.py — US Census ACS5 reference data by county (connId 'census-acs').

Reference data is a STANDALONE constellation, not something baked into outlets: this pulls the
ACS in THREE thematic packs, each landing as its own dataset (its own node in the estate model),
all county-keyed (name + geoid) so any of them JOINS to the outlet master on demand:

  census_demographic — population, median age, households, race/ethnicity
  census_economic    — household + per-capita income, poverty, labor force, unemployment
  census_housing     — median home value + gross rent, units, owner/renter occupancy

One Socrata-free Census API call per state (ACS allows many variables at once), split into the
three datasets. Free key REQUIRED (CENSUS_API_KEY). Stdlib-only, same (datasets,[run],movement)
contract; self-reports degraded/failed with clear warnings.
"""
import os, json, time, hashlib, urllib.request, urllib.parse

YEAR    = os.environ.get("CENSUS_ACS_YEAR", "2023")
DATASET = "acs/acs5"
BASE    = "https://api.census.gov/data"

# thematic packs — (ACS variable code, human column name). All single-value ACS5 estimates.
PACKS = {
    "demographic": [("B01001_001E", "population"), ("B01002_001E", "median_age"),
                    ("B11001_001E", "households"), ("B03003_003E", "hispanic_pop"),
                    ("B02001_002E", "white_pop"), ("B02001_003E", "black_pop"), ("B02001_005E", "asian_pop")],
    "economic":    [("B19013_001E", "median_household_income"), ("B19301_001E", "per_capita_income"),
                    ("B17001_002E", "poverty_pop"), ("B23025_002E", "labor_force"), ("B23025_005E", "unemployed")],
    "housing":     [("B25077_001E", "median_home_value"), ("B25064_001E", "median_gross_rent"),
                    ("B25001_001E", "housing_units"), ("B25003_002E", "owner_occupied"), ("B25003_003E", "renter_occupied")],
}
DEFAULT_STATES = ["12", "48", "17"]   # FL / TX / IL — the book's footprint. state='us' → all counties.

def _all_vars():
    return [v for pack in PACKS.values() for v in pack]

def _fetch(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+census reference connector)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def _url(state_fips):
    params = {"get": "NAME," + ",".join(v[0] for v in _all_vars()), "for": "county:*"}
    if state_fips and state_fips != "us":
        params["in"] = "state:" + state_fips
    q = urllib.parse.urlencode(params)
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if key:
        q += "&key=" + urllib.parse.quote(key)
    return "%s/%s/%s?%s" % (BASE, YEAR, DATASET, q)

def _clean(v):
    try:
        if v is None or float(v) <= -1e8:   # Census jam/annotation sentinels
            return ""
    except (TypeError, ValueError):
        pass
    return v

def pull(state=None, log=print):
    started = int(time.time() * 1000)
    states = DEFAULT_STATES if not state else (["us"] if str(state).lower() in ("us", "all") else [str(state)])
    # accumulate a per-county record: geoid -> {name, state, county, <var>: val}
    counties, warnings = {}, []
    for st in states:
        url = _url(st)
        log("census: GET %s (state=%s)" % (url.split("?")[0], st))
        try:
            raw = _fetch(url)
        except Exception as e:
            warnings.append("fetch failed for state %s: %s" % (st, str(e)[:120])); continue
        if raw.lstrip()[:1] == "<" or ("valid" in raw.lower() and "key" in raw.lower()):
            warnings.append("Census API key required — set CENSUS_API_KEY (free at census.gov/developers/)"); continue
        try:
            data = json.loads(raw)
        except Exception as e:
            warnings.append("parse failed for state %s: %s" % (st, str(e)[:120])); continue
        if not (isinstance(data, list) and len(data) >= 2):
            warnings.append("unexpected shape for state %s" % st); continue
        cols = data[0]
        try:
            i_name, i_st, i_co = cols.index("NAME"), cols.index("state"), cols.index("county")
            vix = {name: cols.index(code) for code, name in _all_vars()}
        except ValueError as e:
            warnings.append("missing column for state %s: %s" % (st, e)); continue
        for rec in data[1:]:
            geoid = (rec[i_st] or "") + (rec[i_co] or "")
            row = {"name": rec[i_name], "state_fips": rec[i_st], "county_fips": rec[i_co], "geoid": geoid}
            for name, j in vix.items():
                row[name] = _clean(rec[j])
            counties[geoid] = row
    n = len(counties)
    st_status = "failed" if not n else ("degraded" if warnings else "success")
    # split into the three thematic datasets, each county-keyed
    datasets, extracts = {}, []
    for pack, vars_ in PACKS.items():
        head = ["name"] + [name for _, name in vars_] + ["state_fips", "county_fips", "geoid"]
        rows = [[c["name"]] + [c.get(name, "") for _, name in vars_] + [c["state_fips"], c["county_fips"], c["geoid"]]
                for c in counties.values()]
        did = "census_" + pack
        datasets[did] = {"header": head, "rows": rows[:800], "total": len(rows), "_rows_full": rows}
        extracts.append({"id": did, "rows": len(rows), "delta": 0, "status": st_status})
    rid = "R-CEN" + hashlib.sha1((str(n) + str(states)).encode()).hexdigest()[:3].upper()
    run = {"id": rid, "connId": "census-acs", "startedAt": started, "finishedAt": int(time.time() * 1000),
           "durationMs": 0, "status": st_status, "trigger": "manual", "total": n,
           "degraded": st_status == "degraded", "warnings": warnings, "healed": [], "extracts": extracts}
    log("census: %d counties across %s → 3 packs, status %s" % (n, states, st_status))
    return datasets, [run], {"sampled": n}

def build(state=None, log=print):
    """Land the ACS packs to the warehouse — census_demographic / census_economic / census_housing,
    each county-keyed by geoid — so census-acs runs on the REGISTRY path (run_sources.run_one) like
    every other source instead of only populating the in-memory console preview. Uses
    write_accumulate (never write_parquet): a re-pull replaces each county's row and keeps the rest,
    so a partial/errored per-state pull can't shrink a good table. An all-empty pull RAISES (missing/
    invalid CENSUS_API_KEY or API down) rather than landing 0 rows over a populated table."""
    import warehouse
    datasets, _runs, _ = pull(state=state, log=log)
    landed, total = [], 0
    for did, d in datasets.items():
        rows = [dict(zip(d["header"], r)) for r in d.get("_rows_full", [])]
        if not rows:
            continue
        res = warehouse.write_accumulate(did, rows, key=lambda r: r["geoid"], fields=d["header"])
        total += res["rows"]; landed.append(did)
        log("%s: %d rows -> %s" % (did, res["rows"], res.get("uri", "")))
    if not landed:
        raise RuntimeError("census-acs build: 0 counties landed (missing/invalid CENSUS_API_KEY or the "
                           "Census API is down) — refusing to land empty ACS tables")
    return {"rows": total, "datasets": landed}


def main(argv=None):
    ds, runs, _ = pull()
    print(json.dumps({k: v for k, v in runs[0].items() if k != "extracts"}, indent=2))
    for did, d in ds.items():
        print(did, "→", d["total"], "counties |", d["header"])

if __name__ == "__main__":
    main()
