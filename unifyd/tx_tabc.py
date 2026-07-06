"""tx_tabc.py — Texas TABC licenses connector (connId 'tx-tabc').

Second state after FL. TABC publishes license data on data.texas.gov (Socrata) — a clean public
JSON API that datacenter IPs CAN reach (unlike myfloridalicense.com / ttbonline.gov), so it works
on Fly. We pull ACTIVE RETAIL licenses and split them into the two master entities:
  - tx_outlets   — one row per licensed location (trade_name + address + COUNTY + license_type)
  - tx_companies — distinct owner / licensee, with its outlet count

`county` is present, so tx_outlets joins to census demographics exactly like FL. Stdlib-only, same
(datasets,[run],movement) contract; self-reports `degraded`/`failed` with warnings.
"""
import json, time, hashlib, urllib.request, urllib.parse

RESOURCE = "https://data.texas.gov/resource/7hf9-qc9f.json"   # TABC License Information
FIELDS   = ["license_id", "trade_name", "owner", "master_file_id", "address", "city", "county",
            "state", "zip", "license_type", "tier", "primary_status"]
OHEAD    = ["license_id", "trade_name", "owner", "address", "city", "county", "state", "zip",
            "license_type", "primary_status", "master_file_id"]
PAGE     = 50000

def _get(params):
    url = RESOURCE + "?" + urllib.parse.urlencode(params, safe="$,()'=%<>! ")
    req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+TX TABC reference connector)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def pull(status="Active", tier="Retail", cap=250000, log=print):
    started = int(time.time() * 1000)
    where = " AND ".join(c for c in [
        ("primary_status='%s'" % status) if status else None,
        ("tier='%s'" % tier) if tier else None] if c)
    outlets, warnings, off = [], [], 0
    try:
        while off < cap:
            batch = _get({"$select": ",".join(FIELDS), "$where": where,
                          "$order": "license_id", "$limit": PAGE, "$offset": off})
            if not batch:
                break
            for d in batch:
                outlets.append([d.get(k, "") for k in OHEAD])
            off += len(batch); log("tx-tabc: %d rows…" % off)
            if len(batch) < PAGE:
                break
    except Exception as e:
        warnings.append("fetch failed at offset %d: %s" % (off, str(e)[:150]))
    # companies = distinct licensee (owner), with outlet count
    io, im = OHEAD.index("owner"), OHEAD.index("master_file_id")
    comp = {}
    for r in outlets:
        key = (r[io] or "").strip()
        if not key:
            continue
        c = comp.setdefault(key, {"owner": key, "master_file_id": r[im], "outlets": 0})
        c["outlets"] += 1
    crows = [[c["owner"], c["master_file_id"], c["outlets"]]
             for c in sorted(comp.values(), key=lambda x: -x["outlets"])]
    chead = ["owner", "master_file_id", "outlets"]
    n = len(outlets)
    st = "failed" if not outlets else ("degraded" if warnings else "success")
    rid = "R-TX" + hashlib.sha1(str(n).encode()).hexdigest()[:3].upper()
    run = {"id": rid, "connId": "tx-tabc", "startedAt": started, "finishedAt": int(time.time() * 1000),
           "durationMs": 0, "status": st, "trigger": "manual", "total": n,
           "degraded": st == "degraded", "warnings": warnings, "healed": [],
           "extracts": [{"id": "tx_outlets", "rows": n, "delta": 0, "status": st},
                        {"id": "tx_companies", "rows": len(crows), "delta": 0, "status": st}]}
    datasets = {"tx_outlets":   {"header": OHEAD, "rows": outlets[:800], "total": n, "_rows_full": outlets},
                "tx_companies": {"header": chead, "rows": crows[:800], "total": len(crows), "_rows_full": crows}}
    log("tx-tabc: %d outlets, %d companies, status %s" % (n, len(crows), st))
    return datasets, [run], {"sampled": n}

def main(argv=None):
    ds, runs, _ = pull(cap=PAGE)   # one page for a quick smoke test
    print(json.dumps({k: v for k, v in runs[0].items() if k != "extracts"}, indent=2)[:500])
    o = ds["tx_outlets"]; print("outlets:", o["total"], "| header:", o["header"])
    for r in o["rows"][:3]:
        print("  ", r[:7])
    print("companies:", ds["tx_companies"]["total"])

if __name__ == "__main__":
    main()
