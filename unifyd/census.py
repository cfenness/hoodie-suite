"""census.py — US Census ACS5 demographics connector (connId 'census-acs').

A REFERENCE-DATA source, not a scrape: the Census Data API is a documented public JSON API,
so this pulls through the same rows/dataset pipeline as the other connectors rather than the
HTML scraper. We fetch a CURATED PACK of the demographic variables a distributor rep actually
cares about, BY COUNTY, and land it as a dataset joinable to the outlet master via county FIPS
(the `geoid` column). Free API key optional (CENSUS_API_KEY) — Census allows keyless pulls at
low volume; a key just raises the rate limit.

Stdlib-only. Same (datasets, [run], movement) contract as abc_fws / ttb_cola. Self-reports
`degraded` (with warnings[]) if the API shape drifts or a state fails, instead of emitting junk.
"""
import os, json, time, hashlib, urllib.request, urllib.parse

YEAR    = os.environ.get("CENSUS_ACS_YEAR", "2023")
DATASET = "acs/acs5"
BASE    = "https://api.census.gov/data"

# curated demographic pack — (ACS variable code, human column name). All single-value ACS5 estimates.
VARS = [
    ("B01001_001E", "population"),
    ("B01002_001E", "median_age"),
    ("B19013_001E", "median_household_income"),
    ("B19301_001E", "per_capita_income"),
    ("B11001_001E", "households"),
    ("B25077_001E", "median_home_value"),
    ("B25064_001E", "median_gross_rent"),
]

# default footprint (FIPS) — the states the book's outlets live in today (FL / TX / IL).
# Pass state="us" for all counties nationally, or a single FIPS ("12") to scope one state.
DEFAULT_STATES = ["12", "48", "17"]

def _fetch(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+census reference connector)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def _build_url(state_fips=None):
    params = {"get": "NAME," + ",".join(v[0] for v in VARS), "for": "county:*"}
    if state_fips and state_fips != "us":
        params["in"] = "state:" + state_fips
    q = urllib.parse.urlencode(params)
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if key:
        q += "&key=" + urllib.parse.quote(key)
    return "%s/%s/%s?%s" % (BASE, YEAR, DATASET, q)

def _clean(v):
    # Census encodes "no data" as large negative jam values (-666666666, -999999999, …). Null those.
    try:
        if v is None: return ""
        if float(v) <= -1e8: return ""
    except (TypeError, ValueError):
        pass
    return v

def pull(state=None, log=print):
    started = int(time.time() * 1000)
    states = DEFAULT_STATES if not state else (["us"] if str(state).lower() in ("us", "all") else [str(state)])
    header = ["name"] + [v[1] for v in VARS] + ["state_fips", "county_fips", "geoid"]
    rows, warnings = [], []
    for st in states:
        url = _build_url(st)
        log("census: GET %s (state=%s)" % (url.split("?")[0], st))
        try:
            raw = _fetch(url)
        except Exception as e:
            warnings.append("fetch failed for state %s: %s" % (st, str(e)[:120])); continue
        # the Census Data API requires a (free) key; without/with a bad one it returns an HTML page
        if raw.lstrip()[:1] == "<" or "valid" in raw.lower() and "key" in raw.lower():
            if "missing key" in raw.lower() or "must be included" in raw.lower():
                warnings.append("Census API key required — set CENSUS_API_KEY (free at census.gov/developers/)")
            elif "invalid key" in raw.lower():
                warnings.append("Census API key rejected — check CENSUS_API_KEY")
            else:
                warnings.append("non-JSON response for state %s" % st)
            continue
        try:
            data = json.loads(raw)
        except Exception as e:
            warnings.append("parse failed for state %s: %s" % (st, str(e)[:120])); continue
        if not (isinstance(data, list) and len(data) >= 2 and isinstance(data[0], list)):
            warnings.append("unexpected response shape for state %s" % st); continue
        cols = data[0]
        try:
            i_name, i_state, i_county = cols.index("NAME"), cols.index("state"), cols.index("county")
            var_ix = [cols.index(v[0]) for v in VARS]
        except ValueError as e:
            warnings.append("missing expected column for state %s: %s" % (st, e)); continue
        for rec in data[1:]:
            sfips, cfips = rec[i_state], rec[i_county]
            rows.append([rec[i_name]] + [_clean(rec[j]) for j in var_ix] + [sfips, cfips, (sfips or "") + (cfips or "")])
    status = "failed" if not rows else ("degraded" if warnings else "success")
    n = len(rows)
    rid = "R-CEN" + hashlib.sha1((str(n) + str(states)).encode()).hexdigest()[:3].upper()
    run = {"id": rid, "connId": "census-acs", "startedAt": started, "finishedAt": int(time.time() * 1000),
           "durationMs": 0, "status": status, "trigger": "manual", "total": n,
           "degraded": status == "degraded", "warnings": warnings, "healed": [],
           "extracts": [{"id": "census_acs", "rows": n, "delta": 0, "status": status}]}
    datasets = {"census_acs": {"header": header, "rows": rows[:800], "total": n, "_rows_full": rows}}
    log("census: %d county rows across %s, status %s" % (n, states, status))
    return datasets, [run], {"sampled": n}

def main(argv=None):
    ds, runs, _ = pull()
    print(json.dumps({k: v for k, v in runs[0].items() if k != "extracts"}, indent=2))
    d = ds["census_acs"]; print("header:", d["header"])
    for r in d["rows"][:4]: print(r)

if __name__ == "__main__":
    main()
