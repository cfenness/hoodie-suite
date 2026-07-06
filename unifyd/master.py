"""master.py — unify the per-state outlet pulls into ONE normalized outlet master.

Each state connector lands its own outlet table in its own schema (FL DBPR = FL_HEADER,
TX TABC = tx_outlets). This normalizes them to a single `outlets_master` schema so the whole
book resolves on one outlet spine — the base table the coverage map + census-join-at-scale need.

Pure/stdlib. `build(sources)` takes {dataset_id: {header, rows}} (full rows) and returns the
master {header, rows, total, by_state, sources}. Add a state = add a mapper + a SOURCES entry.
"""
import hashlib

MASTER_HEADER = ["master_id", "name", "owner", "address", "city", "county",
                 "state", "zip", "license_type", "license_num", "source",
                 "latitude", "longitude", "county_fips"]

def _n(v):
    return str(v if v is not None else "").strip()

def _mapper(colmap, default_state, label, cf_default=None):
    """Return a fn(header, rows) → normalized rows, mapping master field → source column name.
    Carries geocoded latitude/longitude/county_fips through so the master renders on the coverage
    map and joins to Census. cf_default backfills county_fips for a source with no FIPS column but a
    single known county (Chicago = Cook 17031)."""
    def go(header, rows):
        idx = {h.lower(): i for i, h in enumerate(header)}
        def g(r, key):
            i = idx.get((colmap.get(key) or "").lower())
            return _n(r[i]) if (i is not None and i < len(r)) else ""
        out = []
        for r in rows:
            out.append([g(r, "name"), g(r, "owner"), g(r, "address"), g(r, "city"), g(r, "county"),
                        g(r, "state") or default_state, g(r, "zip"), g(r, "license_type"),
                        g(r, "license_num"), label,
                        g(r, "lat"), g(r, "lng"), g(r, "county_fips") or (cf_default or "")])
        return out
    return go

# dataset_id → (mapper, label). Each mapper aligns that state's columns to the master schema.
_GEO = {"lat": "latitude", "lng": "longitude", "county_fips": "county_fips"}   # geocoder-appended cols
SOURCES = {
    # FL isn't geocoded yet (source behind Cloudflare) → no lat/lng; it lands but won't plot until geocoded.
    "bd4006lic": (_mapper({"name": "DBA", "owner": "Owner Name", "address": "Location Address 1",
                           "city": "Location City", "county": "Location County", "state": "Location State",
                           "zip": "Location ZIP", "license_num": "License Number"}, "FL", "FL DBPR"), "FL DBPR"),
    "tx_outlets": (_mapper({"name": "trade_name", "owner": "owner", "address": "address", "city": "city",
                           "county": "county", "state": "state", "zip": "zip", "license_type": "license_type",
                           "license_num": "license_id", **_GEO}, "TX", "TX TABC"), "TX TABC"),
    "il_outlets": (_mapper({"name": "dba", "owner": "owner", "address": "address", "city": "city",
                           "county": "county", "state": "state", "zip": "zip", "license_type": "license_types",
                           "license_num": "outlet_id", **_GEO}, "IL", "Chicago", cf_default="17031"), "Chicago"),
    "ct_outlets": (_mapper({"name": "dba", "owner": "backer", "address": "address", "city": "city",
                           "state": "state", "zip": "zip", "license_type": "credential",
                           "license_num": "credential", **_GEO}, "CT", "CT DCP"), "CT DCP"),
}

def build(sources):
    """sources: {dataset_id: {header, rows}} with FULL rows. Returns the outlet master."""
    rows, by_state, used = [], {}, []
    for did, (mapper_fn, label) in SOURCES.items():
        d = sources.get(did)
        if not isinstance(d, dict) or not d.get("rows"):
            continue
        rows.extend(mapper_fn(d.get("header") or [], d.get("rows") or [])); used.append(label)
    out = []
    for m in rows:   # m = [name, owner, address, city, county, state, zip, license_type, license_num, source]
        mid = "O" + hashlib.sha1(("|".join([m[5], m[8], m[0], m[2]])).encode()).hexdigest()[:11]
        out.append([mid] + m)
        by_state[m[5] or "?"] = by_state.get(m[5] or "?", 0) + 1
    return {"header": MASTER_HEADER, "rows": out, "total": len(out),
            "by_state": dict(sorted(by_state.items(), key=lambda kv: -kv[1])), "sources": used}
