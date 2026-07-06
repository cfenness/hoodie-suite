"""ct_dcp.py — Connecticut liquor data (data.ct.gov Socrata). connId 'ct-dcp'.

CT is a license state with no open PRICE book (those are a DCP web scrape), but it publishes two
clean Socrata datasets we harvest here — "grab what we can, refine later":
  - ct_outlets   — ACTIVE liquor PERMITS (dba + backer + address, geocodable) = the licensee spine.
                   Keeps `credential` (type-prefixed, e.g. LCR./LGB./LIW.) so retail can be filtered
                   downstream; includes all credential types, not just retail.
  - ct_brands    — CT Liquor Brands registry (brand + out-of-state shipper + wholesaler) = a
                   product/supplier spine (market-presence-adjacent: registered+shipped in CT).
  - ct_companies — distinct backer (owner) with permit count.

Socrata JSON API, datacenter-reachable (works on Fly). Stdlib-only; same (datasets,[run],movement)
contract; self-reports degraded/failed with warnings.
"""
import json, time, hashlib, urllib.request, urllib.parse

PERMITS = "https://data.ct.gov/resource/gwv2-eswx.json"     # Liquor Permits (licensees)
BRANDS  = "https://data.ct.gov/resource/u6ds-fzyp.json"     # Liquor Brands (products/suppliers)
PAGE    = 50000
OHEAD   = ["credential", "dba", "backer", "address", "city", "state", "zip", "status", "effective", "expire"]
BHEAD   = ["brand_name", "registration", "shipper", "wholesaler", "status", "effective", "expiration"]


def _get(url, params):
    u = url + "?" + urllib.parse.urlencode(params, safe="$,()'=%<>! ")
    req = urllib.request.Request(u, headers={"User-Agent": "HoodieUnifyd/1.0 (+CT DCP connector)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _page(url, select, where, order, cap, log, tag):
    rows, off = [], 0
    while off < cap:
        batch = _get(url, {"$select": select, "$where": where, "$order": order,
                           "$limit": PAGE, "$offset": off})
        if not batch:
            break
        rows.extend(batch); off += len(batch); log("%s: %d rows…" % (tag, off))
        if len(batch) < PAGE:
            break
    return rows


def pull(cap=300000, log=print):
    started = int(time.time() * 1000)
    warnings = []
    # --- outlets: active permits, deduped to current by credential ---
    outlets = {}
    try:
        # ACTIVE premise permits only — exclude LBD (brand registrations; those are ct_brands) and
        # LSL (supervisor/individual permits, not a premise). The rest are establishments w/ addresses.
        where = ("status='ACTIVE' AND backer_state='CT' "     # CT-located premises (drops out-of-state shippers)
                 "AND NOT starts_with(credential,'LBD') AND NOT starts_with(credential,'LSL')")
        for d in _page(PERMITS, "dba,backer,backer_address,backer_city,backer_state,backer_zip,credential,status,effective_date,expire_date",
                       where, "credential", cap, log, "ct-permits"):
            cred = (d.get("credential") or "").strip()
            key = cred or (d.get("dba", "") + "|" + d.get("backer_address", ""))
            cur = outlets.get(key)
            if not cur or (d.get("effective_date", "") > cur.get("effective_date", "")):
                outlets[key] = d
    except Exception as e:
        warnings.append("permits fetch failed: %s" % str(e)[:140])
    orows = [[o.get("credential", ""), o.get("dba", ""), o.get("backer", ""), o.get("backer_address", ""),
              o.get("backer_city", ""), o.get("backer_state", "") or "CT", o.get("backer_zip", ""),
              o.get("status", ""), o.get("effective_date", ""), o.get("expire_date", "")]
             for o in outlets.values()]
    orows.sort(key=lambda r: r[2])

    # --- brands: active brand registrations ---
    brows = []
    try:
        for d in _page(BRANDS, "brand_name,ct_registration_number,out_of_state_shipper,wholesalers,status,effective,expiration",
                       "status='ACTIVE'", "brand_name", cap, log, "ct-brands"):
            brows.append([d.get("brand_name", ""), d.get("ct_registration_number", ""),
                          d.get("out_of_state_shipper", ""), d.get("wholesalers", ""),
                          d.get("status", ""), d.get("effective", ""), d.get("expiration", "")])
    except Exception as e:
        warnings.append("brands fetch failed: %s" % str(e)[:140])

    # --- companies: distinct backer ---
    comp = {}
    for r in orows:
        k = (r[2] or "").strip()
        if k:
            comp[k] = comp.get(k, 0) + 1
    crows = [[k, v] for k, v in sorted(comp.items(), key=lambda kv: -kv[1])]

    no = len(orows)
    st = "failed" if (not orows and not brows) else ("degraded" if warnings else "success")
    rid = "R-CT" + hashlib.sha1(str(no).encode()).hexdigest()[:3].upper()
    run = {"id": rid, "connId": "ct-dcp", "startedAt": started, "finishedAt": int(time.time() * 1000),
           "durationMs": 0, "status": st, "trigger": "manual", "total": no,
           "degraded": st == "degraded", "warnings": warnings, "healed": [],
           "extracts": [{"id": "ct_outlets", "rows": no, "delta": 0, "status": st},
                        {"id": "ct_brands", "rows": len(brows), "delta": 0, "status": st},
                        {"id": "ct_companies", "rows": len(crows), "delta": 0, "status": st}]}
    datasets = {"ct_outlets":   {"header": OHEAD, "rows": orows[:800], "total": no, "_rows_full": orows},
                "ct_brands":    {"header": BHEAD, "rows": brows[:800], "total": len(brows), "_rows_full": brows},
                "ct_companies": {"header": ["backer", "permits"], "rows": crows[:800], "total": len(crows), "_rows_full": crows}}
    log("ct-dcp: %d outlets, %d brands, %d companies, status %s" % (no, len(brows), len(crows), st))
    return datasets, [run], {"sampled": no}


def main(argv=None):
    ds, runs, _ = pull(cap=PAGE)
    print(json.dumps({k: v for k, v in runs[0].items() if k != "extracts"}, indent=2)[:400])
    o = ds["ct_outlets"]; print("outlets:", o["total"], "| header:", o["header"])
    for r in o["rows"][:3]:
        print("  ", r[:8])
    print("brands:", ds["ct_brands"]["total"], "| companies:", ds["ct_companies"]["total"])


if __name__ == "__main__":
    main()
