"""cpi_ref.py — BLS CPI-U alcoholic beverages: the DEFLATOR / benchmark layer.

Two jobs ([[economic-data-layer]]):
  • Real terms — deflate shelf prices (retail_observations, the price-index app) and demand $ so a
    price move can be split into "inflation" vs "actually more expensive".
  • Benchmark — our own bev-alc shelf-price index has an official yardstick: when Hoodie's index and
    BLS CPI diverge, that divergence is the story (assortment shift / premiumization / promo depth),
    not an error bar.

Source: the official BLS timeseries API, keyless OK — same client discipline as cex_ref.py
([[reference-data-connectors]]). CPI-U NSA series id = CUUR + area + item. Scope (validated live
2026-07): items SA0 (all items — the deflation base), SAF116 (alcoholic beverages), SEFW (at home)
with sub-items SEFW01 beer / SEFW02 distilled spirits / SEFW03 wine, SEFX (away from home — no
published sub-items); areas US + the four census regions (SA0 + SAF116 only at region grain).
Monthly + M13 annual averages. Anchor: US all-items 2023 annual = 304.702.

The premiumization tell baked into the data: away-from-home alcohol (SEFX 429 in 2024) has inflated
FAR faster than at-home (SEFW 229) on the same 1982-84=100 base, and wine at home (182.6) has barely
moved — relative real prices, not nominal, are the signal. query(real=True) serves any alcohol series
REBASED against all-items for exactly that read.

    python cpi_ref.py            # smoke: print the series set (no network)
    python cpi_ref.py --build    # land cpi_reference (keyless OK)
"""
import json, os, sys, time, urllib.request

API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

CPI_HEADER = ["dataset", "series_id", "item_code", "item_name", "area_code", "area_name",
              "vintage_year", "period", "metric_value", "source_pulled_at"]

ITEMS = {  # CPI-U item code -> friendly name (sub-items validated live; SEFX has no sub-items)
    "SA0":    "all_items",
    "SAF116": "alcoholic_beverages",
    "SEFW":   "alcohol_at_home",
    "SEFW01": "beer_at_home",
    "SEFW02": "spirits_at_home",
    "SEFW03": "wine_at_home",
    "SEFX":   "alcohol_away_from_home",
}
AREAS = {"0000": "us", "0100": "northeast", "0200": "midwest", "0300": "south", "0400": "west"}
# Region grain only publishes the top-line items; the full item set is US-only.
_REGION_ITEMS = ("SA0", "SAF116")

# Anchor: CPI-U all items, US, 2023 annual average (M13) — a fixed published figure (NSA is not revised).
_ANCHOR = {"sid": "CUUR0000SA0", "year": 2023, "value": 304.702}


def _series():
    out = []
    for area in AREAS:
        for item in (ITEMS if area == "0000" else _REGION_ITEMS):
            out.append(("CUUR%s%s" % (area, item), area, item))
    return out


def _key():
    return os.environ.get("BLS_API_KEY", "").strip()


def _post(series_ids, start, end):
    body = {"seriesid": series_ids, "startyear": str(start), "endyear": str(end), "annualaverage": True}
    if _key():
        body["registrationkey"] = _key()
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "HoodieUnifyd/1.0 (+cpi reference)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError("BLS API: %s %s" % (payload.get("status"), payload.get("message")))
    return payload


def fetch(start=None, end=None):
    """One windowed POST per 25-series chunk (15 series today). Keyless window cap is 10 years."""
    end = end or time.localtime().tm_year
    start = start or end - 9
    ids = [s for s, _, _ in _series()]
    chunk = 50 if _key() else 25
    return [_post(ids[i:i + chunk], start, end) for i in range(0, len(ids), chunk)]


def parse(payloads, ts=None):
    """BLS payloads -> long/tall rows (one per series × year × period, M01..M12 + M13 annual).
    Pure for the frozen-fixture regression check."""
    ts = ts or int(time.time())
    by_id = {s: (a, i) for s, a, i in _series()}
    rows, warns, seen = [], [], set()
    for p in payloads:
        for m in p.get("message") or []:
            if "does not exist" in str(m):
                warns.append("BLS: %s" % m)
        for s in (p.get("Results") or {}).get("series") or []:
            sid = s.get("seriesID", "")
            if sid not in by_id:
                continue
            area, item = by_id[sid]
            seen.add(sid)
            for d in s.get("data") or []:
                try:
                    val = float(str(d.get("value", "")).replace(",", ""))
                except ValueError:
                    continue                       # CPI has no suppression; a dash is just not-yet-published
                rows.append(dict(zip(CPI_HEADER, [
                    "cpi", sid, item, ITEMS[item], area, AREAS[area],
                    int(d["year"]), d.get("period", ""), val, ts])))
    missing = set(by_id) - seen
    if missing:
        warns.append("%d/%d series absent (e.g. %s)" % (len(missing), len(by_id), sorted(missing)[0]))
    return rows, warns


def _check(rows):
    """Anchor: US all-items 2023 annual must equal the published figure (NSA CPI is never revised —
    any drift means the series/base changed and every downstream real-terms figure is wrong)."""
    a = next((r for r in rows if r["series_id"] == _ANCHOR["sid"]
              and r["vintage_year"] == _ANCHOR["year"] and r["period"] == "M13"), None)
    if a is None:
        return ["anchor cell %s %d M13 not in pull (window too narrow?)" % (_ANCHOR["sid"], _ANCHOR["year"])]
    if abs(a["metric_value"] - _ANCHOR["value"]) > 0.01 * _ANCHOR["value"]:
        return ["ANCHOR MISMATCH: %s %d M13 = %.3f, published %.3f — series base changed? real-terms "
                "math downstream is suspect" % (_ANCHOR["sid"], _ANCHOR["year"], a["metric_value"], _ANCHOR["value"])]
    return []


def build(log=print):
    """Fetch + parse + anchor-check -> MERGE into cpi_reference (write_accumulate; a re-pull refreshes
    each series×year×period cell in place, late months simply appear). Honest failure on empty."""
    import warehouse
    try:
        payloads = fetch()
    except Exception as e:
        log("cpi_ref: BLS API unreachable (%s) — nothing landed [DEGRADED]" % type(e).__name__)
        return {"rows": 0, "uri": warehouse.uri("cpi_reference"),
                "warnings": ["BLS fetch failed: %s" % e], "degraded": True}
    rows, warns = parse(payloads)
    warns += _check(rows)
    if not rows:
        log("cpi_ref: 0 rows parsed — nothing landed [DEGRADED]")
        return {"rows": 0, "uri": warehouse.uri("cpi_reference"), "warnings": warns, "degraded": True}
    res = warehouse.write_accumulate(
        "cpi_reference", rows, fields=CPI_HEADER,
        key=lambda r: (r["dataset"], r["series_id"], r["vintage_year"], r["period"]))
    degraded = bool(warns)
    log("cpi_reference: %d rows -> %s%s" % (res["rows"], res["uri"], "  [DEGRADED]" if degraded else ""))
    for w in warns:
        log("  warn: %s" % w)
    return {"rows": res["rows"], "uri": res["uri"], "warnings": warns, "degraded": degraded}


def query(item=None, area=None, year=None, period=None, annual=False, limit=5000):
    """Read cpi_reference by item code / area code / year. annual=True -> M13 rows only."""
    import warehouse
    where, params = ["1=1"], []
    for col, val in (("item_code", item), ("area_code", area), ("vintage_year", year), ("period", period)):
        if val not in (None, ""):
            where.append("%s = ?" % col); params.append(val)
    if annual:
        where.append("period = 'M13'")
    sql = ("SELECT * FROM t WHERE " + " AND ".join(where)
           + " ORDER BY vintage_year, period LIMIT %d" % int(limit))
    return warehouse.query("cpi_reference", sql, params)


def real_series(item="SAF116", area="0000", base_year=None):
    """The alcohol item REBASED against all-items — the relative real-price series. >100 = alcohol
    got more expensive than everything else since the base year; <100 = relatively cheaper (the
    premiumization/affordability tell). Base = earliest common M13 year unless given."""
    alc = {r["vintage_year"]: r["metric_value"] for r in query(item=item, area=area, annual=True)}
    all_i = {r["vintage_year"]: r["metric_value"] for r in query(item="SA0", area=area, annual=True)}
    years = sorted(set(alc) & set(all_i))
    if not years:
        return {"ok": False, "reason": "cpi_reference not landed for item %s area %s" % (item, area)}
    base = base_year if base_year in years else years[0]
    rel0 = alc[base] / all_i[base]
    return {"ok": True, "item": item, "item_name": ITEMS.get(item, item), "area": AREAS.get(area, area),
            "base_year": base,
            "series": [{"year": y, "cpi": alc[y], "all_items": all_i[y],
                        "real_relative": round(100.0 * (alc[y] / all_i[y]) / rel0, 1)} for y in years],
            "note": "real_relative = item CPI / all-items CPI, rebased to 100 at %d — relative real "
                    "price, the inflation-free signal" % base}


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
    else:
        print("CPI series (%d, keyless window 10y):" % len(_series()))
        for sid, area, item in _series():
            print("  %-16s %-9s %s" % (sid, AREAS[area], ITEMS[item]))
        print("\nanchor: %s %d M13 = %.3f (published, NSA never revised)"
              % (_ANCHOR["sid"], _ANCHOR["year"], _ANCHOR["value"]))
