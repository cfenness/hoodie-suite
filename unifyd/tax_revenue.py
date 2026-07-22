"""tax_revenue.py — beverage-alcohol TAX REVENUE (collections) reference layer.

The dollars actually COLLECTED — the market-size / demand axis, distinct from the per-unit schedule in
tax_rates.py. Long/tall time series, keyed (source, jurisdiction, fiscal_year, tax_kind, beverage_class),
append-only so each year's figure is its own row and a re-pull refreshes in place ([[reference-data-connectors]]).

Two authoritative, free sources — no scrape:
  • CENSUS STC (state) — the Annual Survey of State Government Tax Collections via the governments
    timeseries API (SVY_COMP=STC). Two alcohol-specific item codes: T10 = Alcoholic Beverages Sales Tax,
    T20 = Alcoholic Beverages License. Per state + US, by year. Reuses CENSUS_API_KEY and the same
    header-name parsing discipline as census_ref.py — validate LIVE on Fly (keyless -> "Missing Key" HTML).
    The govs `AMOUNT` field is reported in THOUSANDS of dollars (Census government-finance convention;
    confirmed against FRED's Census-sourced T10 series — e.g. CA FY2021 = $411,969 thousand). Stored raw
    with unit="USD_thousands", _AMOUNT_SCALE=1. Every live pull SELF-CHECKS this via _verify_scale() against
    a known anchor cell and warns if the magnitude instead looks like whole dollars (a 1000x margin error is
    loud, not silent) — so no manual scale confirmation is needed.
  • TTB (federal) — TTB's tax-collections statistical reports, by commodity (distilled spirits / wine /
    beer). TTB is TLS-blocked from Fly, so refresh_ttb() only succeeds on a network-capable host (the Mac);
    on Fly it self-reports degraded and lands nothing federal rather than emitting fabricated numbers.

Honest failure: no key -> Census yields nothing and build() is degraded (not a silent empty catalog);
TTB unreachable -> degraded federal, Census still lands.

    python tax_revenue.py             # smoke: print the exact Census call it will make (no key needed)
    python tax_revenue.py --build     # land (needs CENSUS_API_KEY; TTB needs live network)
"""
import json, os, sys, time, urllib.request, urllib.parse

REV_HEADER = [
    "source",               # census_stc | ttb
    "jurisdiction_level",   # federal | state
    "jurisdiction",         # US | 2-letter state (from FIPS)
    "fiscal_year",          # int
    "tax_kind",             # sales | license | excise
    "beverage_class",       # all | spirits | wine | beer
    "metric_name",          # collections
    "metric_value",         # float
    "unit",                 # USD_thousands | USD
    "suppressed",           # bool — value withheld/unavailable, never treated as 0
    "provenance",           # verified
    "source_pulled_at",     # epoch
    "notes",
]

# Census govs alcohol tax item codes (Government Finance & Employment Classification):
#   T10 = Alcoholic Beverages Sales Tax   ·   T20 = Alcoholic Beverages License
CENSUS_ALC = {"T10": ("sales", "Alcoholic Beverages Sales Tax"),
              "T20": ("license", "Alcoholic Beverages License")}
_AMOUNT_SCALE = 1                     # raw thousands (Census convention, confirmed); _verify_scale() self-checks each pull
_FIPS = {  # state FIPS -> USPS (govs returns numeric state FIPS in GEO_ID/`state`)
    "01":"AL","02":"AK","04":"AZ","05":"AR","06":"CA","08":"CO","09":"CT","10":"DE","11":"DC","12":"FL",
    "13":"GA","15":"HI","16":"ID","17":"IL","18":"IN","19":"IA","20":"KS","21":"KY","22":"LA","23":"ME",
    "24":"MD","25":"MA","26":"MI","27":"MN","28":"MS","29":"MO","30":"MT","31":"NE","32":"NV","33":"NH",
    "34":"NJ","35":"NM","36":"NY","37":"NC","38":"ND","39":"OH","40":"OK","41":"OR","42":"PA","44":"RI",
    "45":"SC","46":"SD","47":"TN","48":"TX","49":"UT","50":"VT","51":"VA","53":"WA","54":"WV","55":"WI","56":"WY"}


# Scale self-check anchor: Census STC T10 (Alcoholic Beverages Sales Tax) for CA, FY2021 = $411,969 THOUSAND
# (FRED CAALCOTAX / QTAXT10…, Census-sourced). A live pull must land ~this in `metric_value` (thousands). If it
# instead lands ~1000x that, the API switched to whole dollars and unit/_AMOUNT_SCALE must be revisited.
_ANCHOR = {"jur": "CA", "year": 2021, "kind": "sales", "thousands": 411969.0}


def _verify_scale(rows, log=print):
    """Confirm the govs AMOUNT scale against the anchor cell that's already in the pull (no extra API call).
    Returns [] when confirmed thousands, else a loud warning. Distinguishing thousands vs dollars is a 1000x
    gap, so an 8% band cleanly separates them while tolerating Census revisions."""
    a = next((r for r in rows if r.get("jurisdiction") == _ANCHOR["jur"]
              and r.get("fiscal_year") == _ANCHOR["year"] and r.get("tax_kind") == _ANCHOR["kind"]
              and r.get("metric_value") not in ("", None)), None)
    if not a:
        return ["scale self-check skipped: anchor %s/FY%d/T10 not in this pull" % (_ANCHOR["jur"], _ANCHOR["year"])]
    val, exp = float(a["metric_value"]), _ANCHOR["thousands"]
    if abs(val - exp) <= 0.08 * exp:
        log("tax_revenue: AMOUNT scale confirmed THOUSANDS (anchor %s FY%d = %.0f ~ %.0f)"
            % (_ANCHOR["jur"], _ANCHOR["year"], val, exp))
        return []
    if abs(val - exp * 1000) <= 0.08 * exp * 1000:
        return ["SCALE MISMATCH: anchor %s FY%d landed %.0f (~whole dollars, expected %.0f thousands) — Census "
                "AMOUNT changed scale; set unit=USD and reconcile _AMOUNT_SCALE" % (_ANCHOR["jur"], _ANCHOR["year"], val, exp)]
    return ["scale anchor off: %s FY%d landed %.0f vs %.0f thousands (Census revision or vintage drift?) — verify"
            % (_ANCHOR["jur"], _ANCHOR["year"], val, exp)]


def _key():
    return os.environ.get("CENSUS_API_KEY", "").strip()


def census_url(agg_desc, year):
    """The exact govs STC call (documented so a keyed run is reproducible by hand)."""
    p = {"get": "AMOUNT,GEO_ID,YEAR,AGG_DESC", "for": "state:*", "time": str(year),
         "SVY_COMP": "STC", "AGG_DESC": agg_desc}
    if _key():
        p["key"] = _key()
    return "https://api.census.gov/data/timeseries/govs?" + urllib.parse.urlencode(p)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+tax revenue)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read().decode("utf-8", "replace")
    if not body.strip() or body.lstrip().startswith("<"):
        raise RuntimeError("non-JSON (missing key?)")
    return json.loads(body)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pull_census(years, log=print):
    """Census STC alcohol collections (T10 sales, T20 license), per state, for each year. Returns
    (rows, warnings). No key -> empty + warning (honest, not a silent 0-catalog)."""
    rows, warns, ts = [], [], int(time.time())
    if not _key():
        return rows, ["CENSUS_API_KEY unset — no state revenue pulled (run on Fly / with key)"]
    for year in years:
        for code, (kind, label) in CENSUS_ALC.items():
            try:
                data = _get(census_url(code, year))
            except Exception as e:
                warns.append("census %s %s: %s" % (code, year, type(e).__name__)); continue
            if not data or len(data) < 2:
                warns.append("census %s %s: 0 rows" % (code, year)); continue
            hdr = data[0]
            for row in data[1:]:
                r = dict(zip(hdr, row))
                fips = (r.get("state") or (r.get("GEO_ID") or "")[-2:] or "").zfill(2)
                amt = _num(r.get("AMOUNT"))
                rows.append(dict(
                    source="census_stc", jurisdiction_level="state", jurisdiction=_FIPS.get(fips, fips),
                    fiscal_year=int(year), tax_kind=kind, beverage_class="all", metric_name="collections",
                    metric_value=(amt * _AMOUNT_SCALE if amt is not None else ""),
                    unit=("USD_thousands" if _AMOUNT_SCALE == 1 else "USD"),
                    suppressed=(amt is None), provenance="verified", source_pulled_at=ts,
                    notes="Census govs STC %s (%s)" % (code, label)))
        log("tax_revenue: census year %s done (%d rows so far)" % (year, len(rows)))
    warns += _verify_scale(rows, log=log)   # self-confirm the AMOUNT scale on every live pull
    return rows, warns


def refresh_ttb(log=print):
    """TTB federal collections by commodity. TTB is TLS-blocked from Fly, so this only lands on a
    network-capable host (the Mac). Rather than seed fabricated dollar figures, it lands NOTHING when TTB
    is unreachable and reports degraded — a missing federal series must look missing.
    Statistics hub: https://www.ttb.gov/resources/statistics"""
    import urllib.request
    url = "https://www.ttb.gov/resources/statistics"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+tax revenue)"})
        urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:
        return [], ["TTB collections unreachable (%s) — run live on the Mac to land federal series" % type(e).__name__]
    # Live TTB reachable: the commodity report parse is a Mac-run follow-up (report layout varies by
    # year/format); wire the specific report URL here once confirmed live. Kept explicit, not fabricated.
    log("tax_revenue: TTB reachable — commodity-report parse is the live-run follow-up")
    return [], ["TTB reachable but commodity parse not yet wired — no fabricated federal figures landed"]


def build(years=None, log=print):
    """Land Census STC (state) + TTB (federal, live-only). Returns {rows, warnings, degraded}."""
    import warehouse
    years = years or list(range(2018, 2024))   # STC vintages; trim/extend as new years publish
    rows, warns = pull_census(years, log=log)
    trows, tw = refresh_ttb(log=log)
    rows += trows; warns += tw
    if not rows:
        log("tax_revenue: 0 rows (no key / all sources unreachable) — nothing landed [DEGRADED]")
        return {"rows": 0, "uri": warehouse.uri("tax_revenue"), "warnings": warns, "degraded": True}
    def key(r):
        return (r["source"], r["jurisdiction"], r["fiscal_year"], r["tax_kind"], r["beverage_class"])
    res = warehouse.write_accumulate("tax_revenue", rows, key=key, fields=REV_HEADER)
    degraded = bool(warns)
    log("tax_revenue: %d rows -> %s%s" % (res["rows"], res["uri"], "  [DEGRADED]" if degraded else ""))
    for w in warns:
        log("  warn: %s" % w)
    return {"rows": res["rows"], "uri": res["uri"], "warnings": warns, "degraded": degraded}


def query(source=None, jurisdiction=None, tax_kind=None, year=None, limit=5000):
    import warehouse
    where, params = ["1=1"], []
    for col, val in (("source", source), ("jurisdiction", jurisdiction), ("tax_kind", tax_kind),
                     ("fiscal_year", year)):
        if val not in (None, ""):
            where.append("%s = ?" % col); params.append(val)
    sql = "SELECT * FROM t WHERE " + " AND ".join(where) + " ORDER BY fiscal_year LIMIT %d" % int(limit)
    return warehouse.query("tax_revenue", sql, params)


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
    else:
        print("Census STC alcohol collections — exact calls build() will make:")
        for code, (kind, label) in CENSUS_ALC.items():
            print("  %s (%-7s) %s" % (code, kind, census_url(code, 2021).replace("&key=" + _key(), "&key=…" if _key() else "")))
        print("\nkey set: %s" % ("yes" if _key() else "NO — set CENSUS_API_KEY to land state revenue"))
        print("TTB federal: live-only (TLS-blocked on Fly) — run on the Mac to land the commodity series")
