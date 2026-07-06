"""chicago.py — Chicago liquor-licensed outlets connector (connId 'il-chicago').

Third jurisdiction after FL + TX. Chicago publishes Business Licenses on data.cityofchicago.org
(Socrata) — a clean public JSON API datacenter IPs CAN reach, so it works on Fly. We pull ACTIVE
(license_status='AAI') liquor licenses — Package Goods (off-premise), Tavern + Consumption on
Premises (on-premise) — and split them into:
  - il_outlets   — one row per licensed LOCATION (account+site), deduped, liquor types joined.
                   Includes latitude/longitude straight from the portal (no geocoding needed) and
                   the source license_status, so 'active' is known at the source.
  - il_companies — distinct owner (legal_name), with its outlet count.

All Chicago -> county is Cook, so il_outlets is census-joinable like FL/TX. Stdlib-only; same
(datasets,[run],movement) contract; self-reports degraded/failed with warnings.
"""
import json, time, hashlib, urllib.request, urllib.parse

RESOURCE = "https://data.cityofchicago.org/resource/r5kz-chrr.json"   # Business Licenses
LIQUOR   = ["Consumption on Premises - Incidental Activity", "Tavern", "Package Goods"]
FIELDS   = ["account_number", "site_number", "license_number", "legal_name",
            "doing_business_as_name", "address", "city", "state", "zip_code",
            "license_description", "license_status", "license_start_date", "expiration_date",
            "latitude", "longitude", "ward", "community_area_name"]
OHEAD    = ["outlet_id", "dba", "owner", "address", "city", "county", "state", "zip",
            "license_types", "status", "latitude", "longitude", "ward", "community_area",
            "license_start", "expiration"]
PAGE     = 50000


def _get(params):
    url = RESOURCE + "?" + urllib.parse.urlencode(params, safe="$,()'=%<>! ")
    req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+Chicago liquor connector)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def pull(cap=300000, log=print):
    started = int(time.time() * 1000)
    where = "license_status='AAI' AND license_description in(%s)" % ",".join("'%s'" % d for d in LIQUOR)
    warnings, off = [], 0
    # dedupe by (account_number, site_number): a store often holds several liquor licenses
    outlets = {}
    try:
        while off < cap:
            batch = _get({"$select": ",".join(FIELDS), "$where": where,
                          "$order": "account_number,site_number", "$limit": PAGE, "$offset": off})
            if not batch:
                break
            for d in batch:
                oid = "%s-%s" % (d.get("account_number", ""), d.get("site_number", ""))
                o = outlets.get(oid)
                if not o:
                    o = {"outlet_id": oid,
                         "dba": d.get("doing_business_as_name") or d.get("legal_name", ""),
                         "owner": d.get("legal_name", ""),
                         "address": d.get("address", ""), "city": d.get("city", ""),
                         "county": "Cook", "state": d.get("state", ""), "zip": d.get("zip_code", ""),
                         "types": set(), "status": "Active",
                         "latitude": d.get("latitude", ""), "longitude": d.get("longitude", ""),
                         "ward": d.get("ward", ""), "community_area": d.get("community_area_name", ""),
                         "license_start": d.get("license_start_date", "") or "",
                         "expiration": d.get("expiration_date", "") or ""}
                    outlets[oid] = o
                o["types"].add(d.get("license_description", ""))
                # keep the latest expiration seen for this outlet
                if (d.get("expiration_date") or "") > o["expiration"]:
                    o["expiration"] = d.get("expiration_date", "")
            off += len(batch); log("il-chicago: %d license rows, %d outlets…" % (off, len(outlets)))
            if len(batch) < PAGE:
                break
    except Exception as e:
        warnings.append("fetch failed at offset %d: %s" % (off, str(e)[:150]))

    orows = [[o["outlet_id"], o["dba"], o["owner"], o["address"], o["city"], o["county"],
              o["state"], o["zip"], ";".join(sorted(t for t in o["types"] if t)), o["status"],
              o["latitude"], o["longitude"], o["ward"], o["community_area"],
              o["license_start"], o["expiration"]]
             for o in outlets.values()]
    orows.sort(key=lambda r: r[2])   # by owner

    # companies = distinct owner with outlet count
    comp = {}
    for r in orows:
        key = (r[2] or "").strip()
        if not key:
            continue
        comp[key] = comp.get(key, 0) + 1
    crows = [[k, v] for k, v in sorted(comp.items(), key=lambda kv: -kv[1])]
    chead = ["owner", "outlets"]

    n = len(orows)
    st = "failed" if not orows else ("degraded" if warnings else "success")
    rid = "R-IL" + hashlib.sha1(str(n).encode()).hexdigest()[:3].upper()
    run = {"id": rid, "connId": "il-chicago", "startedAt": started, "finishedAt": int(time.time() * 1000),
           "durationMs": 0, "status": st, "trigger": "manual", "total": n,
           "degraded": st == "degraded", "warnings": warnings, "healed": [],
           "extracts": [{"id": "il_outlets", "rows": n, "delta": 0, "status": st},
                        {"id": "il_companies", "rows": len(crows), "delta": 0, "status": st}]}
    datasets = {"il_outlets":   {"header": OHEAD, "rows": orows[:800], "total": n, "_rows_full": orows},
                "il_companies": {"header": chead, "rows": crows[:800], "total": len(crows), "_rows_full": crows}}
    log("il-chicago: %d outlets, %d companies, status %s" % (n, len(crows), st))
    return datasets, [run], {"sampled": n}


def main(argv=None):
    ds, runs, _ = pull(cap=PAGE)
    print(json.dumps({k: v for k, v in runs[0].items() if k != "extracts"}, indent=2)[:400])
    o = ds["il_outlets"]; print("outlets:", o["total"], "| header:", o["header"])
    for r in o["rows"][:3]:
        print("  ", r[:10])
    print("companies:", ds["il_companies"]["total"], "top:", ds["il_companies"]["rows"][:3])


if __name__ == "__main__":
    main()
