"""enrich.py — join reference data onto the outlet master.

Reference-data pulls (census, later BLS/FRED) aren't a master FEED (nothing to reconcile by
beverage class) — their job is to ENRICH. This does the first one: attach ACS county
demographics (`census_acs`) to each outlet by county + state, so every account carries its
trade-area numbers into HI's Demographics view.

Join key: outlets have "Location County"/"Location State" (FL DBPR) or a normalized `county`/
`state` (places connector); census rows carry NAME ("Orange County, Florida"). We normalize both
to (county, state-abbrev) and match. Pure functions — unit-testable without the live engine.
"""
import re

US_STATES = {
    "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA","colorado":"CO",
    "connecticut":"CT","delaware":"DE","district of columbia":"DC","florida":"FL","georgia":"GA",
    "hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA","kansas":"KS","kentucky":"KY",
    "louisiana":"LA","maine":"ME","maryland":"MD","massachusetts":"MA","michigan":"MI","minnesota":"MN",
    "mississippi":"MS","missouri":"MO","montana":"MT","nebraska":"NE","nevada":"NV","new hampshire":"NH",
    "new jersey":"NJ","new mexico":"NM","new york":"NY","north carolina":"NC","north dakota":"ND",
    "ohio":"OH","oklahoma":"OK","oregon":"OR","pennsylvania":"PA","rhode island":"RI","south carolina":"SC",
    "south dakota":"SD","tennessee":"TN","texas":"TX","utah":"UT","vermont":"VT","virginia":"VA",
    "washington":"WA","west virginia":"WV","wisconsin":"WI","wyoming":"WY","puerto rico":"PR",
}

def _norm_county(s):
    s = (s or "").strip().lower()
    s = re.sub(r",.*$", "", s)   # "orange county, florida" → "orange county"
    s = re.sub(r"\s+(county|parish|borough|census area|city and borough|municipio|city)$", "", s)
    return re.sub(r"[^a-z]", "", s)   # "st. lucie"→"stlucie", "miami-dade"→"miamidade", "de kalb"→"dekalb"

def _state_abbr(v):
    v = (v or "").strip()
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return US_STATES.get(v.lower(), "")

def _census_state(name):
    m = re.search(r",\s*([A-Za-z .]+)\s*$", name or "")   # "…County, Florida" → FL
    return _state_abbr(m.group(1).strip()) if m else ""

def _find(header, candidates):
    low = [h.lower() for h in header]
    for c in candidates:
        if c.lower() in low:
            return low.index(c.lower())
    return None

_CENSUS_KEYS = ("name", "state_fips", "county_fips", "geoid")

def merge_census(packs):
    """Merge the thematic census datasets (demographic/economic/housing), each county-keyed, into
    ONE {header, rows} by geoid — so a single join attaches every reference figure to an outlet."""
    merged, demo_cols = {}, []
    for d in packs:
        h = d.get("header") or []
        if "geoid" not in h:
            continue
        gi = h.index("geoid")
        cols = [c for c in h if c not in _CENSUS_KEYS]
        for c in cols:
            if c not in demo_cols:
                demo_cols.append(c)
        for r in (d.get("rows") or []):
            g = r[gi] if gi < len(r) else ""
            if not g:
                continue
            rec = merged.setdefault(g, {})
            for k in _CENSUS_KEYS:
                if k in h:
                    rec[k] = r[h.index(k)]
            for c in cols:
                rec[c] = r[h.index(c)]
    header = ["name"] + demo_cols + ["state_fips", "county_fips", "geoid"]
    rows = [[rec.get("name", "")] + [rec.get(c, "") for c in demo_cols]
            + [rec.get("state_fips", ""), rec.get("county_fips", ""), rec.get("geoid", "")]
            for rec in merged.values()]
    return {"header": header, "rows": rows}

def join_census_to_outlets(census, outlets):
    """Return an enriched outlet table + match stats. `census`/`outlets` are {header, rows}."""
    ch, crows = census.get("header") or [], census.get("rows") or []
    oh, orows = outlets.get("header") or [], outlets.get("rows") or []
    i_name = _find(ch, ["name", "NAME"])
    if i_name is None or not crows:
        return {"error": "census dataset missing a county 'name' column or empty"}
    demo_cols = [c for c in ch if c not in ("name", "state_fips", "county_fips", "geoid")]
    demo_ix = [ch.index(c) for c in demo_cols]
    # census index: (normalized county, state) -> demographic values
    idx = {}
    for r in crows:
        nm = r[i_name] if i_name < len(r) else ""
        key = (_norm_county(nm), _census_state(nm))
        if key[0] and key[1]:
            idx[key] = [r[j] if j < len(r) else "" for j in demo_ix]
    oc = _find(oh, ["Location County", "county", "County", "Mail County"])
    os_ = _find(oh, ["Location State", "state", "State", "Mail State"])
    if oc is None or os_ is None:
        return {"error": "outlet dataset has no county/state column to join on"}
    header = list(oh) + demo_cols
    rows, matched = [], 0
    for r in orows:
        cc = _norm_county(r[oc] if oc < len(r) else "")
        ss = _state_abbr(r[os_] if os_ < len(r) else "")
        hit = idx.get((cc, ss))
        if hit:
            matched += 1
        rows.append(list(r) + (hit if hit else [""] * len(demo_cols)))
    return {"header": header, "rows": rows, "matched": matched, "total": len(orows),
            "counties_indexed": len(idx), "demo_cols": demo_cols}


def _selftest():
    census = {"header": ["name", "population", "median_household_income", "state_fips", "county_fips", "geoid"],
              "rows": [["Orange County, Florida", "1452726", "72201", "12", "095", "12095"],
                       ["Miami-Dade County, Florida", "2673837", "60146", "12", "086", "12086"],
                       ["Harris County, Texas", "4780913", "69193", "48", "201", "48201"]]}
    outlets = {"header": ["DBA", "Location City", "Location State", "Location County", "License Number"],
               "rows": [["Joe's Bar", "Orlando", "FL", "Orange", "BEV12345"],
                        ["Miami Spirits", "Miami", "FL", "Miami-Dade", "BEV22222"],
                        ["Houston Wine", "Houston", "TX", "Harris", "BEV33333"],
                        ["Nowhere Liquor", "?", "FL", "Nonexistent", "BEV99999"]]}
    out = join_census_to_outlets(census, outlets)
    assert "error" not in out, out
    assert out["matched"] == 3 and out["total"] == 4, out
    assert "population" in out["header"] and "median_household_income" in out["header"]
    joe = out["rows"][0]
    assert joe[-2] == "1452726", joe   # Orange County population attached
    print("enrich selftest OK — matched %d/%d, cols +%s" % (out["matched"], out["total"], out["demo_cols"]))

if __name__ == "__main__":
    _selftest()
