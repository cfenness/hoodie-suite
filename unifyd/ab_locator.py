#!/usr/bin/env python3
"""ab_locator.py — Anheuser-Busch InBev retailer locator (connId `ab-inbev`).

WHY THIS IS THE UNIVERSAL-OUTLET SPINE:
  Budweiser / Bud Light are carried in ~every alcohol outlet in the US, so AB InBev's own
  "where to buy" API is effectively a national outlet census. It's a plain GraphQL endpoint
  (`api.beertech.com/singularity/graphql`) with **no auth** — just brandName + zipCode + radius:
      locateRetailers(brandName, limit, offset, zipCode, radius) {
        retailers { vpid name address city state zipCode latitude longitude distance }
      }
  Each outlet carries a **`vpid`** — a VIP (Vermont Information Processing) outlet id, the SAME
  identity spine behind the VTInfo finder ([[chain-inventory-acquisition]]) — so AB outlets
  cross-reference VTInfo/other-brand pulls for the confirm+conform master layer.

CONTRACT (reverse-engineered live, all confirmed):
  - No API key/token/cookie. Only origin/referer headers.
  - `limit` caps at 100; page with `offset` (0,100,200…) until a short page. Paging is clean
    (0 overlap between pages).
  - `radius` in miles. Brands are AB-OWNED US names: BUDWEISER, BUD LIGHT, MICHELOB ULTRA,
    BUSCH, … (Modelo/Stella = Constellation in the US → 0 here).
  - We query each brand × each zip, page fully, and **dedup by vpid** — accumulating which AB
    brands each outlet carries (a real carriage signal). Snapshot+diff for repeatability.
stdlib only. Same (datasets,[run],movement) contract as the other owned connectors.
"""
import argparse, hashlib, json, os, time, urllib.request, urllib.error

URL = "https://api.beertech.com/singularity/graphql"
SNAP = "ab_locator_snapshot.json"
HEADER = ["VPID", "Name", "Address", "City", "State", "Zip", "Lat", "Lng", "AB_Brands", "Zips_Hit"]
# AB InBev owned US brands whose locator returns outlets (verified: these return; others 0/null).
DEFAULT_BRANDS = ["BUDWEISER", "BUD LIGHT", "MICHELOB ULTRA", "BUSCH", "BUSCH LIGHT",
                  "MICHELOB ULTRA PURE GOLD", "STELLA ARTOIS", "BUD LIGHT NEXT"]
_HEADERS = {"content-type": "application/json", "accept": "*/*",
            "origin": "https://us.budweiser.com", "referer": "https://us.budweiser.com/",
            "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")}

_Q = ('query{ locateRetailers(brandName:"%s", limit:100, offset:%d, zipCode:"%s", radius:%s){ '
      'retailers{ vpid name address city state zipCode latitude longitude distance } } }')


def _gql(query, timeout=30):
    req = urllib.request.Request(URL, data=json.dumps({"query": query}).encode(), headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def locate(brand, zip_code, radius=25.0, max_pages=20, delay=0.3, log=print):
    """Every outlet carrying `brand` near `zip_code`, paged fully via offset."""
    out, offset = [], 0
    for _ in range(max_pages):
        try:
            d = _gql(_Q % (brand, offset, zip_code, radius))
        except urllib.error.HTTPError as e:
            log("  %s@%s offset %d: HTTP %s" % (brand, zip_code, offset, e.code)); break
        except Exception as e:
            log("  %s@%s offset %d: %s" % (brand, zip_code, offset, str(e)[:70])); break
        block = ((d.get("data") or {}).get("locateRetailers") or {}).get("retailers")
        if not block:
            break
        out.extend(block)
        if len(block) < 100:
            break
        offset += 100
        if delay:
            time.sleep(delay)
    return out


def pull(zips=None, brands=None, radius=25.0, delay=0.3, out=".", state_dir=None, log=print):
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    started = int(time.time() * 1000)
    zips = zips or ["32819"]
    brands = brands or DEFAULT_BRANDS
    outlets, warns = {}, []          # vpid -> merged outlet record (+ brand carriage, zips hit)
    for z in zips:
        for b in brands:
            try:
                rows = locate(b, z, radius=radius, delay=delay, log=log)
            except Exception as e:
                warns.append("%s@%s: %s" % (b, z, str(e)[:80])); continue
            for r in rows:
                vp = str(r.get("vpid") or "")
                if not vp:
                    continue
                o = outlets.get(vp)
                if not o:
                    o = {"vpid": vp, "name": r.get("name", ""), "address": r.get("address", ""),
                         "city": r.get("city", ""), "state": r.get("state", ""),
                         "zip": r.get("zipCode", ""), "lat": r.get("latitude"), "lng": r.get("longitude"),
                         "brands": set(), "zips": set()}
                    outlets[vp] = o
                o["brands"].add(b); o["zips"].add(str(z))
        log("ab-inbev: %s → %d distinct outlets so far" % (z, len(outlets)))

    prev = {}
    sp = os.path.join(state_dir, SNAP)
    if os.path.exists(sp):
        try: prev = json.load(open(sp)).get("cells", {})
        except Exception: prev = {}
    changed = sum(1 for vp in outlets if vp not in prev)
    # persist a JSON-safe snapshot (sets -> sorted lists)
    cells = {vp: {**{k: v for k, v in o.items() if k not in ("brands", "zips")},
                  "brands": sorted(o["brands"]), "zips": sorted(o["zips"])} for vp, o in outlets.items()}
    json.dump({"__ts__": started, "cells": cells}, open(sp, "w"))

    rows = [[o["vpid"], o["name"], o["address"], o["city"], o["state"], o["zip"],
             o["lat"], o["lng"], ", ".join(sorted(o["brands"])), ", ".join(sorted(o["zips"]))]
            for o in sorted(outlets.values(), key=lambda x: (x["state"], x["city"], x["name"]))]
    status = "degraded" if (warns and not outlets) else "success"
    ds = {"ab_outlets": {"header": HEADER, "rows": rows[:2000], "total": len(rows), "_rows_full": rows}}
    run = _run(len(outlets), changed, status, warns, started)
    log("ab-inbev: %d distinct outlets across %d zips × %d brands (%d new), status %s"
        % (len(outlets), len(zips), len(brands), changed, status))
    return ds, [run], {"sampled": len(outlets), "changed": changed}


def _run(n, changed, status, warnings, started):
    return {"id": "R-AB" + hashlib.sha1(str(n).encode()).hexdigest()[:3].upper(), "connId": "ab-inbev",
            "startedAt": started, "finishedAt": int(time.time() * 1000), "durationMs": 0,
            "status": status, "trigger": "manual", "total": n, "degraded": status != "success",
            "warnings": warnings, "healed": [],
            "extracts": [{"id": "ab_outlets", "rows": n, "delta": changed, "status": status}]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Anheuser-Busch InBev retailer locator (universal-outlet spine).")
    ap.add_argument("--zips", default="32819", help="comma-separated ZIPs")
    ap.add_argument("--brands", default="", help="comma-separated AB brand names (default: the owned set)")
    ap.add_argument("--radius", type=float, default=25.0); ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--out", default="./ab_out")
    a = ap.parse_args(argv)
    brands = [b.strip() for b in a.brands.split(",") if b.strip()] or None
    ds, runs, mv = pull(zips=[z.strip() for z in a.zips.split(",") if z.strip()], brands=brands,
                        radius=a.radius, delay=a.delay, out=a.out, state_dir=a.out)
    r = list(ds.values())[0]
    for row in r["rows"][:15]:
        print("  •", row[1][:30], "|", row[3], row[4], "|", row[8])
    print("outlets:", runs[0]["total"], "| movement:", mv)


if __name__ == "__main__":
    main()
