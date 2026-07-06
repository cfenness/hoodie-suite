"""control_state.py — harvest control-state PRODUCT / PRICE / SALES data into the warehouse.

Control ("ABC") states run the stores themselves, so instead of outlet licenses they publish the
thing license states can't: product catalogs, official price books, and often transaction/period
SALES. That's a different, higher-value axis than the outlet map — it feeds the item master, a
pricing reference, and a real DEMAND signal (which also corroborates COLA tiering, [[cola-tiering]]).

This lands each source as its own warehouse dataset (Parquet on Tigris, queried by DuckDB —
[[warehouse-and-snowflake]]); some are 100k–300k+ rows, past the in-memory JSON state store. Sources
are Socrata open-data (datacenter-reachable, so they run on Fly). Discovery = the Socrata federated
catalog (api.us.socrata.com/api/catalog/v1); this CATALOG is the vetted subset worth taking now.

    python control_state.py --build            # land all (on a box with the Tigris creds, e.g. Fly)
    python control_state.py --build or_pricing # just one
"""
import json, sys, time, urllib.request, urllib.parse

PAGE = 50000

# name → (domain, 4x4 id, human label). Vetted from the control-state survey (2026-07-06).
CATALOG = {
    # Montgomery County MD runs its own distribution → the full product→price→inventory→sales chain.
    "mont_sales":   ("data.montgomerycountymd.gov", "v76h-r7br", "Montgomery MD — monthly warehouse+retail sales by product"),
    "mont_catalog": ("data.montgomerycountymd.gov", "ib5t-5ncy", "Montgomery MD — product catalog + price + on-hand inventory"),
    "mont_purch":   ("data.montgomerycountymd.gov", "kpfd-c5d5", "Montgomery MD — product purchases by DLC"),
    # Oregon OLCC statewide price book (proof + size + per-bottle/case/oz), monthly.
    "or_pricing":   ("data.oregon.gov", "vmf2-f83h", "Oregon OLCC — monthly product pricing"),
}

# CKAN portals that publish the price book as a CSV file (not a Socrata API). Canada's provinces are
# government monopolies → same product/price axis as US control states. We resolve the LATEST CSV
# resource at build time so it tracks the monthly refresh instead of pinning a stale file URL.
CKAN_CSV = {
    "bc_liquor": {"ckan": "https://catalogue.data.gov.bc.ca",
                  "package": "bc-liquor-store-product-price-list-historical-prices",
                  "label": "BC Liquor — product price list (SKU, UPC, ABV, price, category)"},
}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+control-state harvest)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _clean(rec):
    """Drop Socrata's internal computed-region / system columns; keep the real fields."""
    return {k: v for k, v in rec.items()
            if not (k.startswith(":@") or k.startswith(":") or k.startswith("computed_region"))}


def fetch_all(domain, rid, cap=1000000, log=print):
    """Page a Socrata resource fully → list of flat dict records (system columns stripped)."""
    out, off = [], 0
    while off < cap:
        url = "https://%s/resource/%s.json?%s" % (domain, rid, urllib.parse.urlencode(
            {"$limit": PAGE, "$offset": off, "$order": ":id"}))
        batch = _get(url)
        if not batch:
            break
        out.extend(_clean(r) for r in batch)
        off += len(batch)
        log("  %s/%s: %d rows…" % (domain, rid, off))
        if len(batch) < PAGE:
            break
    return out


def build_csv(name, log=print):
    """Land a CKAN CSV source (e.g. BC Liquor) — resolve its latest CSV resource, download, parse,
    write Parquet. Kept schema-agnostic (columns straight from the file header)."""
    import warehouse, csv as _csv, io as _io
    cfg = CKAN_CSV[name]
    meta = _get(cfg["ckan"] + "/api/3/action/package_show?id=" + cfg["package"])["result"]
    csvs = [r for r in meta.get("resources", []) if (r.get("format") or "").upper() == "CSV"]
    if not csvs:
        raise RuntimeError("%s: no CSV resource on the CKAN package" % name)
    csvs.sort(key=lambda r: (r.get("last_modified") or r.get("created") or ""), reverse=True)
    latest = csvs[0]
    log("%s — %s (latest: %s)" % (name, cfg["label"], latest.get("name", "?")))
    req = urllib.request.Request(latest["url"], headers={"User-Agent": "HoodieUnifyd/1.0 (+control-state harvest)"})
    with urllib.request.urlopen(req, timeout=240) as r:
        txt = r.read().decode("utf-8", "replace")
    rows = list(_csv.reader(_io.StringIO(txt)))
    if not rows:
        log("%s: empty CSV" % name); return {"name": name, "rows": 0}
    header = [h.strip() for h in rows[0]]
    data = [r for r in rows[1:] if any((c or "").strip() for c in r)]
    recs = [{header[i]: (r[i] if i < len(r) else "") for i in range(len(header))} for r in data]
    res = warehouse.write_parquet(name, recs, fields=header)
    log("%s: wrote %s rows → %s" % (name, format(res["rows"], ","), res["uri"]))
    return {"name": name, "rows": res["rows"], "uri": res["uri"], "fields": header, "label": cfg["label"]}


def build(name, log=print):
    """Fetch one source and land it as a warehouse Parquet dataset. Dispatches CKAN-CSV vs Socrata."""
    import warehouse
    if name in CKAN_CSV:
        return build_csv(name, log=log)
    if name not in CATALOG:
        raise ValueError("unknown control-state source: %s" % name)
    domain, rid, label = CATALOG[name]
    log("%s — %s" % (name, label))
    recs = fetch_all(domain, rid, log=log)
    if not recs:
        log("%s: no rows" % name)
        return {"name": name, "rows": 0}
    # Union of keys → a stable column set; fill missing per row so the Parquet schema is consistent.
    fields = []
    seen = set()
    for r in recs:
        for k in r:
            if k not in seen:
                seen.add(k); fields.append(k)
    recs = [{f: r.get(f, "") for f in fields} for r in recs]
    res = warehouse.write_parquet(name, recs, fields=fields)
    log("%s: wrote %s rows → %s" % (name, format(res["rows"], ","), res["uri"]))
    return {"name": name, "rows": res["rows"], "uri": res["uri"], "fields": fields, "label": label}


def build_all(only=None, log=print):
    names = [only] if only else (list(CATALOG) + list(CKAN_CSV))
    return [build(n, log=log) for n in names]


if __name__ == "__main__":
    if "--build" in sys.argv:
        one = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
        for r in build_all(only=one):
            print("done:", {k: r.get(k) for k in ("name", "rows")})
    else:
        # dry inspect: row counts + columns, no warehouse write
        for name, (dom, rid, label) in CATALOG.items():
            try:
                c = _get("https://%s/resource/%s.json?$select=count(*)" % (dom, rid))
                n = list(c[0].values())[0]
                cols = list(_clean(_get("https://%s/resource/%s.json?$limit=1" % (dom, rid))[0]).keys())
                print("%-14s %8s rows | %s" % (name, n, cols))
            except Exception as e:
                print("%-14s ERR %s" % (name, str(e)[:70]))
