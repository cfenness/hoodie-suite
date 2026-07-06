"""socrata_outlets.py — ONE generic connector for any Socrata outlet/license feed.

Most states publish their liquor licensees on a Socrata open-data portal (data.<x>.gov), all the
same shape: a REST resource, paginated, address columns, often a built-in point (lat/lng). Rather
than hand-write a module per state (tx_tabc, ct_dcp, …), this connector is CONFIG-DRIVEN: add a
state = add a PORTALS entry (domain + 4x4 id + column map), not a new file. Discovery is the Socrata
federated catalog (api.us.socrata.com/api/catalog/v1?q=liquor+license) — see [[state-master-spine]].

Every portal is normalised to ONE header (SO_HEAD) so master.py maps them all with a single colmap.
Portals that carry a Socrata `point` field (NY georeference, CO location) come pre-geocoded — lat/lng
land straight on the map with no geocode pass; the rest geocode later like FL/TX. Socrata portals are
datacenter-reachable, so these run on Fly. Stdlib-only; standard (datasets,[run],movement) contract.
"""
import json, time, hashlib, urllib.request, urllib.parse

PAGE = 50000
# Canonical normalised outlet row — identical across every portal so master.py needs one mapper.
SO_HEAD = ["name", "owner", "address", "city", "county", "state", "zip",
           "license_type", "license_num", "latitude", "longitude"]

# connId → feed config. `cols` maps SO_HEAD field → this portal's column (missing = blank). `point`
# is a Socrata location field ({"coordinates":[lng,lat]} or {latitude,longitude}); None = geocode later.
# `addr_parts` builds address from multiple columns when there's no single address field.
PORTALS = {
    "ny-sla": {
        "state": "NY", "label": "NY SLA", "ds": "ny_outlets",
        "url": "https://data.ny.gov/resource/9s3h-dpkz.json",
        "cols": {"name": "legalname", "owner": "legalname", "address": "actualaddressofpremises",
                 "city": "city", "county": "premisescounty", "state": "statename", "zip": "zipcode",
                 "license_type": "description", "license_num": "licensepermitid"},
        "point": "georeference", "where": None,
    },
    "co-led": {
        "state": "CO", "label": "CO LED", "ds": "co_outlets",
        "url": "https://data.colorado.gov/resource/ier5-5ms2.json",
        "cols": {"name": "doing_business_as", "owner": "licensee_name", "address": "street_address",
                 "city": "city", "state": "state", "zip": "zip",
                 "license_type": "license_type", "license_num": "license_number"},
        "point": "location", "where": None,
    },
    "mo-atc": {
        "state": "MO", "label": "MO ATC", "ds": "mo_outlets",
        "url": "https://data.mo.gov/resource/yyhn-562y.json",
        "cols": {"name": "dbaname", "owner": "licensee", "city": "city", "state": "state",
                 "zip": "zipcode", "license_type": "primary_type", "license_num": "primary_license"},
        "addr_parts": ["street_number", "street"], "point": None, "where": None,
    },
}

VALID = set(PORTALS)


def _get(url, params):
    u = url + "?" + urllib.parse.urlencode(params, safe="$,()'=%<>! ")
    req = urllib.request.Request(u, headers={"User-Agent": "HoodieUnifyd/1.0 (+socrata outlet connector)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _latlng(rec, field):
    """Pull (lat, lng) from a Socrata point field. Handles GeoJSON ({"coordinates":[lng,lat]}) and
    the legacy location shape ({"latitude":..,"longitude":..})."""
    if not field:
        return "", ""
    v = rec.get(field)
    if isinstance(v, dict):
        c = v.get("coordinates")
        if isinstance(c, list) and len(c) >= 2:
            try:
                return float(c[1]), float(c[0])
            except (TypeError, ValueError):
                return "", ""
        if v.get("latitude") and v.get("longitude"):
            return v["latitude"], v["longitude"]
    return "", ""


def _norm(rec, cfg):
    c = cfg["cols"]
    def g(field):
        return (rec.get(c[field]) or "").strip() if field in c else ""
    if cfg.get("addr_parts"):
        addr = " ".join(str(rec.get(p) or "").strip() for p in cfg["addr_parts"]).strip()
    else:
        addr = g("address")
    lat, lng = _latlng(rec, cfg.get("point"))
    return [g("name"), g("owner"), addr, g("city"), g("county"), g("state") or cfg["state"],
            g("zip"), g("license_type"), g("license_num"), lat, lng]


def pull(conn_id, cap=400000, log=print):
    if conn_id not in PORTALS:
        raise ValueError("unknown socrata portal: %s" % conn_id)
    cfg = PORTALS[conn_id]
    started = int(time.time() * 1000)
    warnings = []
    rows, off = [], 0
    try:
        while off < cap:
            params = {"$limit": PAGE, "$offset": off, "$order": ":id"}
            if cfg.get("where"):
                params["$where"] = cfg["where"]
            batch = _get(cfg["url"], params)
            if not batch:
                break
            rows.extend(_norm(r, cfg) for r in batch)
            off += len(batch)
            log("%s: %d rows…" % (conn_id, off))
            if len(batch) < PAGE:
                break
    except Exception as e:
        warnings.append("fetch failed at offset %d: %s" % (off, str(e)[:140]))

    geocoded = sum(1 for r in rows if r[9] != "" and r[10] != "")
    no = len(rows)
    st = "failed" if not rows else ("degraded" if warnings else "success")
    rid = "R-" + cfg["state"] + hashlib.sha1(str(no).encode()).hexdigest()[:3].upper()
    ds_id = cfg["ds"]
    run = {"id": rid, "connId": conn_id, "startedAt": started, "finishedAt": int(time.time() * 1000),
           "durationMs": 0, "status": st, "trigger": "manual", "total": no,
           "degraded": st == "degraded", "warnings": warnings, "healed": [],
           "extracts": [{"id": ds_id, "rows": no, "delta": 0, "status": st}]}
    datasets = {ds_id: {"header": SO_HEAD, "rows": rows[:800], "total": no, "_rows_full": rows}}
    log("%s: %d outlets (%d pre-geocoded), status %s" % (conn_id, no, geocoded, st))
    return datasets, [run], {"sampled": no, "geocoded": geocoded}


def main(argv=None):
    import sys
    cid = (argv or sys.argv[1:] or ["ny-sla"])[0]
    ds, runs, mv = pull(cid, cap=PAGE)
    print("run:", {k: runs[0][k] for k in ("connId", "status", "total")}, "movement:", mv)
    d = list(ds.values())[0]
    print("header:", d["header"])
    for r in d["rows"][:4]:
        print("  ", r)


if __name__ == "__main__":
    main()
