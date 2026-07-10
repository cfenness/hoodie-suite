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


# ── DIRECT control-state sources (verified live 2026-07-10). Not Socrata/CKAN — each state publishes
# its price book / product list its own way (a search API, a CSV, a monthly XLSX, or HTML tables). One
# fetcher per shape; all land a warehouse Parquet like the rest. Registry CUSTOM = name → builder fn.
import io as _io, csv as _csv, re as _re, datetime as _dt
from urllib.parse import urljoin as _urljoin

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"


def _http(url, headers=None, timeout=120):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _land(name, recs, fields, label, log):
    import warehouse
    recs = [{f: ("" if r.get(f) is None else r.get(f)) for f in fields} for r in recs]
    res = warehouse.write_parquet(name, recs, fields=fields)
    log("%s: wrote %s rows -> %s" % (name, format(res["rows"], ","), res["uri"]))
    return {"name": name, "rows": res["rows"], "uri": res["uri"], "fields": fields, "label": label}


def _xlsx_rows(b):
    """Parse xlsx bytes -> (header, [dict]). Header = first row in the first 15 with >=3 non-empty cells."""
    import openpyxl
    wb = openpyxl.load_workbook(_io.BytesIO(b), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    hi = next((i for i, r in enumerate(rows[:15])
               if sum(1 for c in r if isinstance(c, str) and c.strip()) >= 3), 0)
    header, seen = [], {}
    for j, c in enumerate(rows[hi]):
        h = (str(c).strip() if c is not None else "") or ("col%d" % j)
        if h in seen:
            seen[h] += 1; h = "%s_%d" % (h, seen[h])
        else:
            seen[h] = 0
        header.append(h)
    out = []
    for r in rows[hi + 1:]:
        if not any(c is not None and str(c).strip() for c in r):
            continue
        out.append({header[j]: ("" if (j >= len(r) or r[j] is None) else str(r[j])) for j in range(len(header))})
    return header, out


def _find_xlsx(page_url, must=""):
    html = _http(page_url).decode("utf-8", "replace")
    links = [l for l in _re.findall(r'href=["\']([^"\']+\.xlsx[^"\']*)["\']', html, _re.I)
             if must.lower() in l.lower()]
    return _urljoin(page_url, links[0]) if links else None


def build_idaho(log=print):
    """Idaho ISLD — statewide product + price via the site's public Typesense search API (no anti-bot)."""
    key = "M7jrNg3txJqfirZM2Cjd1xg2DwQ2NlAS"
    base = "https://m7zjux4b6qin5verp-1.a1.typesense.net/collections/products/documents/search"
    fields = ["nabca", "name", "description", "price", "sale_price", "on_sale", "proof", "size",
              "bottles_sold", "cat_id", "of_idaho"]
    out, page = [], 1
    while True:
        u = base + "?" + urllib.parse.urlencode({"q": "*", "query_by": "name", "per_page": 250, "page": page})
        d = json.loads(_http(u, {"x-typesense-api-key": key, "User-Agent": _UA}))
        hits = d.get("hits") or []
        if not hits:
            break
        out.extend({k: (h.get("document") or {}).get(k) for k in fields} for h in hits)
        if page * 250 >= d.get("found", 0):
            break
        page += 1
    return _land("id_products", out, fields, "Idaho ISLD — product + price (Typesense API)", log)


def build_nc(log=print):
    """North Carolina ABC — full quarterly price list, one CSV URL, no auth."""
    txt = _http("https://abc2.nc.gov/Pricing/ExportData").decode("utf-8", "replace")
    rows = list(_csv.reader(_io.StringIO(txt)))
    header = [h.strip() for h in rows[0]]
    data = [{header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
            for r in rows[1:] if any((c or "").strip() for c in r)]
    return _land("nc_pricing", data, header, "North Carolina ABC — quarterly price list", log)


def build_montana(log=print):
    """Montana Liquor Control — monthly 'Price Disk' XLSX (full catalog + price), templated URL."""
    base = "https://revenuefiles.mt.gov/files/Alcoholic-Beverages/Agency-Liquor-Stores/Product-Information/Price-Disks/PriceDisk-%s-%d.xlsx"
    d = _dt.date.today()
    for back in range(0, 4):                                  # current month, then walk back
        m = d.month - back; y = d.year
        while m <= 0:
            m += 12; y -= 1
        url = base % (_dt.date(y, m, 1).strftime("%B"), y)
        try:
            header, recs = _xlsx_rows(_http(url))
            return _land("mt_pricing", recs, header, "Montana — monthly Price Disk (catalog + price)", log)
        except Exception:
            continue
    raise RuntimeError("montana: no Price Disk found for the last 4 months")


def build_utah(log=print):
    """Utah DABS — monthly product-list XLSX at a direct, fiscal-period-templated URL. Utah's FY starts in
    July: month>=Jul -> FY(year+1) Period(month-6); Jan-Jun -> FY(year) Period(month+6). Walk back a few
    periods in case the current one isn't posted yet."""
    base = "https://abs.utah.gov/wp-content/uploads/%s-%d-Product-List-FY%s-P%d.xlsx"
    d = _dt.date.today()
    for back in range(0, 4):
        m = d.month - back; y = d.year
        while m <= 0:
            m += 12; y -= 1
        fy_end = y + 1 if m >= 7 else y
        period = m - 6 if m >= 7 else m + 6
        url = base % (_dt.date(y, m, 1).strftime("%B"), y, str(fy_end)[-2:], period)
        try:
            header, recs = _xlsx_rows(_http(url))
            return _land("ut_pricing", recs, header, "Utah DABS — product list + price", log)
        except Exception:
            continue
    raise RuntimeError("utah: no product-list xlsx found for the last 4 periods")


def build_alabama(log=print):
    """Alabama ABC — quarterly price book XLSX (link discovered off the QPL page)."""
    link = _find_xlsx("https://alabcboard.gov/product-management/QPL", must="price")
    if not link:
        raise RuntimeError("alabama: no price-book xlsx link found")
    header, recs = _xlsx_rows(_http(link))
    return _land("al_pricing", recs, header, "Alabama ABC — quarterly price book", log)


def build_maine(log=print):
    """Maine Spirits (BABLO) — item listing XLSX WITH UPCs (link discovered off the price-books page)."""
    for page in ("https://www.mainespirits.com/price-books", "https://www.mainespirits.com/wholesale"):
        try:
            link = _find_xlsx(page, must="")
            if link:
                header, recs = _xlsx_rows(_http(link))
                return _land("me_pricing", recs, header, "Maine Spirits — item listing (UPC-bearing)", log)
        except Exception:
            continue
    raise RuntimeError("maine: no item-listing xlsx link found")


def build_vermont(log=print):
    """Vermont (802 Spirits) — statewide price tables scraped from the price-guide category pages."""
    cats = ["whiskey", "vodka", "gin", "rum", "tequila", "brandy-cognac", "cordials-liqueurs", "wine"]
    fields = ["category", "code", "brand", "size", "proof", "price", "sale_price"]
    out = []
    for cat in cats:
        try:
            html = _http("https://www.802spirits.com/price_guide/" + cat).decode("utf-8", "replace")
        except Exception:
            continue
        for tr in _re.findall(r"<tr[^>]*>(.*?)</tr>", html, _re.S | _re.I):
            cells = [_re.sub(r"<[^>]+>", "", c).strip() for c in _re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, _re.S | _re.I)]
            cells = [c for c in cells if c]
            if len(cells) >= 4 and any("$" in c for c in cells):
                prices = [c for c in cells if "$" in c]
                out.append(dict(category=cat, code=cells[0], brand=cells[1] if len(cells) > 1 else "",
                                size=next((c for c in cells if _re.search(r"\d\s*(ml|l|L|oz)", c)), ""),
                                proof=next((c for c in cells if _re.search(r"^\d{2,3}$", c)), ""),
                                price=prices[0] if prices else "", sale_price=prices[1] if len(prices) > 1 else ""))
    if not out:
        raise RuntimeError("vermont: no price rows parsed")
    return _land("vt_pricing", out, fields, "Vermont 802 Spirits — statewide price guide", log)


CUSTOM = {"id_products": build_idaho, "nc_pricing": build_nc, "mt_pricing": build_montana,
          "ut_pricing": build_utah, "al_pricing": build_alabama, "me_pricing": build_maine,
          "vt_pricing": build_vermont}


def build(name, log=print):
    """Fetch one source and land it as a warehouse Parquet dataset. Dispatches custom / CKAN-CSV / Socrata."""
    import warehouse
    if name in CUSTOM:
        return CUSTOM[name](log=log)
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
    names = [only] if only else (list(CATALOG) + list(CKAN_CSV) + list(CUSTOM))
    out = []
    for n in names:
        try:
            out.append(build(n, log=log))
        except Exception as e:
            log("%s: FAILED — %s" % (n, str(e)[:120]))
    return out


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
