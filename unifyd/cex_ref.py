"""cex_ref.py — BLS Consumer Expenditure Survey (CEX): alcohol spend by income bracket + trade-area demand $.

The DEMAND-DOLLAR layer ([[economic-data-layer]]): what households actually SPEND on alcohol per year,
cut by income — the multiplier that turns the ACS household-income distribution (census_ref.py) into a
quantitative trade-area demand estimate, with no sales feed required.

Source: the official BLS timeseries API (api.bls.gov/publicAPI/v2) — an API, not a scrape
([[reference-data-connectors]]). Keyless works (25 series/query, 10 years, 25 queries/day);
BLS_API_KEY raises the limits. CX series id = CXU + item + demographic·characteristic + M(ean), e.g.
CXUALCBEVGLB0221M = mean alcohol spend, income-before-taxes $100–149,999. All layout facts below were
validated LIVE (2026-07): the active income-before-taxes DOLLAR brackets live under LB02 with
NON-CONTIGUOUS characteristic codes (18,19,07,08,09,20,21,22,23 — the gaps are discontinued historical
brackets), and LB01 is QUINTILES, not dollars. Items: ALCBEVG (total alcohol) = ALCHOME (at home,
off-premise) + ALCAWAY (away from home, on-premise) — the split maps 1:1 onto Hoodie's account types.
2023 anchor: all-CU alcohol = $637 = 294 + 343, matching the published CEX tables.

Self-checks every build (degraded + warnings, never silent):
  • bracket-placement — the pulled INCBEFTX (mean income of CUs in the bracket) must fall INSIDE the
    bracket's dollar range; a BLS renumbering would put means outside their ranges, and that bracket's
    rows are DROPPED (mislabeled spend is worse than missing spend).
  • home+away identity — ALCHOME + ALCAWAY ≈ ALCBEVG per cell.
  • anchor — all-CU 2023 alcohol = $637 ±8% (a revision drifts, a unit change screams).

Demand $ (the CEX × ACS join, [[signal-stack-cross-bucket]]): demand_for(geo_fips) maps the 16 ACS
B19001 household-income brackets onto the 9 CEX brackets (one straddle: ACS 60–75k splits 2/3:1/3
across the 70k boundary, uniform assumption) and sums households × mean spend. Consumer unit ≈
household is an approximation; means are national (no regional cut yet) — both stated in caveats,
never hidden. build_demand() lands the full county+state layer as trade_area_demand (derived rebuild,
plain write). ACS brackets are key-gated (CENSUS_API_KEY) so demand self-reports not-landed until a
census build has run with the full B19001 set.

    python cex_ref.py                 # smoke: print the exact series ids + limits (no network)
    python cex_ref.py --build         # land cex_reference (keyless OK)
    python cex_ref.py --demand 12095  # trade-area demand $ for one geo (needs ACS brackets landed)
"""
import json, os, sys, time, urllib.request

API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

CEX_HEADER = ["dataset", "vintage_year", "item_code", "item_name", "demographic", "bracket_code",
              "bracket_label", "bracket_lo", "bracket_hi", "metric_name", "metric_value",
              "suppressed", "source_pulled_at"]

ITEMS = {  # CEX item mnemonic -> friendly name (all validated live)
    "ALCBEVG":  "alcohol_total",
    "ALCHOME":  "alcohol_at_home",        # off-premise demand
    "ALCAWAY":  "alcohol_away_from_home", # on-premise demand
    "TOTALEXP": "total_expenditures",     # share-of-wallet denominator
    "INCBEFTX": "income_before_taxes",    # bracket-placement self-check
}

# LB02 characteristic code -> (lo, hi, label). Codes are NON-CONTIGUOUS on purpose (BLS kept the codes
# of surviving brackets when older cuts were discontinued). Verified live: each bracket's pulled mean
# income falls inside its range (e.g. code 21 mean $121,816 ∈ [100k,150k)). "all" = LB0101 all CUs.
BRACKETS = {
    "18": (0,      14999,  "lt_15k"),
    "19": (15000,  29999,  "15k_30k"),
    "07": (30000,  39999,  "30k_40k"),
    "08": (40000,  49999,  "40k_50k"),
    "09": (50000,  69999,  "50k_70k"),
    "20": (70000,  99999,  "70k_100k"),
    "21": (100000, 149999, "100k_150k"),
    "22": (150000, 199999, "150k_200k"),
    "23": (200000, None,   "200k_plus"),
    "all": (0,     None,   "all_consumer_units"),
}

# Anchor: published CEX 2023 all-CU mean annual alcohol spend (= 294 home + 343 away, confirmed live).
_ANCHOR = {"year": 2023, "item": "ALCBEVG", "usd": 637.0}


def _series_id(item, code):
    return "CXU%s%sM" % (item, "LB0101" if code == "all" else "LB02" + code)


def _all_series():
    return [(_series_id(i, c), i, c) for i in ITEMS for c in BRACKETS]


def _key():
    return os.environ.get("BLS_API_KEY", "").strip()


def _post(series_ids, start, end):
    body = {"seriesid": series_ids, "startyear": str(start), "endyear": str(end)}
    if _key():
        body["registrationkey"] = _key()
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "HoodieUnifyd/1.0 (+cex reference)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError("BLS API: %s %s" % (payload.get("status"), payload.get("message")))
    return payload


def fetch(years=None):
    """POST the full series set in API-sized chunks. Keyless caps: 25 series/query, 10 years."""
    end = years[-1] if years else _ANCHOR["year"]
    start = years[0] if years else end - 8
    ids = [s for s, _, _ in _all_series()]
    chunk = 50 if _key() else 25
    return [_post(ids[i:i + chunk], start, end) for i in range(0, len(ids), chunk)]


def parse(payloads, ts=None):
    """BLS payloads -> long/tall rows (one per item × bracket × year). Pure so the frozen fixture can
    regression-check it (deep_audit FIXTURE_CHECKS). Missing series / non-numeric values -> suppressed
    or skipped with a warning, never fabricated."""
    ts = ts or int(time.time())
    by_id = {s: (i, c) for s, i, c in _all_series()}
    rows, warns, seen = [], [], set()
    for p in payloads:
        for m in p.get("message") or []:
            if "does not exist" in str(m):
                warns.append("BLS: %s" % m)
        for s in (p.get("Results") or {}).get("series") or []:
            sid = s.get("seriesID", "")
            if sid not in by_id:
                continue
            item, code = by_id[sid]
            lo, hi, label = BRACKETS[code]
            seen.add(sid)
            for d in s.get("data") or []:
                if d.get("period") not in ("A01", "M13"):   # CEX is annual
                    continue
                try:
                    val = float(str(d.get("value", "")).replace(",", ""))
                except ValueError:
                    val = None
                rows.append(dict(zip(CEX_HEADER, [
                    "cex", int(d["year"]), item, ITEMS[item],
                    "income_before_taxes" if code != "all" else "all_consumer_units",
                    code, label, float(lo), (float(hi) if hi is not None else None),
                    "mean_annual_usd_per_cu", val, val is None, ts])))
    missing = {s for s in by_id} - seen
    if missing:
        warns.append("%d/%d series absent from the response (e.g. %s)"
                     % (len(missing), len(by_id), sorted(missing)[0]))
    return rows, warns


def _check(rows):
    """The three build-time self-checks. Returns (kept_rows, warnings) — a bracket whose pulled mean
    income falls outside its assigned dollar range is DROPPED (BLS renumbered; mislabeled > missing)."""
    warns, drop = [], set()
    cell = {(r["item_code"], r["bracket_code"], r["vintage_year"]): r["metric_value"]
            for r in rows if r["metric_value"] is not None}
    years = sorted({r["vintage_year"] for r in rows})
    # 1) bracket placement (INCBEFTX mean must sit inside the bracket)
    for code, (lo, hi, label) in BRACKETS.items():
        if code == "all":
            continue
        for y in years:
            mean = cell.get(("INCBEFTX", code, y))
            if mean is None:
                continue
            if mean < lo or (hi is not None and mean > hi):
                drop.add(code)
                warns.append("bracket %s (%s) %d: mean income %.0f outside [%s, %s] — BLS renumbering? "
                             "bracket dropped" % (code, label, y, mean, lo, hi if hi is not None else "inf"))
    # 2) home + away identity
    for y in years:
        for code in BRACKETS:
            tot, home, away = (cell.get(("ALCBEVG", code, y)), cell.get(("ALCHOME", code, y)),
                               cell.get(("ALCAWAY", code, y)))
            if None not in (tot, home, away) and abs((home + away) - tot) > max(15.0, 0.03 * tot):
                warns.append("identity %s %d: home %.0f + away %.0f != total %.0f" % (code, y, home, away, tot))
    # 3) anchor
    a = cell.get((_ANCHOR["item"], "all", _ANCHOR["year"]))
    if a is not None and abs(a - _ANCHOR["usd"]) > 0.08 * _ANCHOR["usd"]:
        warns.append("anchor: all-CU %s %d landed %.0f vs published %.0f — BLS revision or unit change, verify"
                     % (_ANCHOR["item"], _ANCHOR["year"], a, _ANCHOR["usd"]))
    kept = [r for r in rows if r["bracket_code"] not in drop]
    return kept, warns


def build(years=None, log=print):
    """Fetch + parse + self-check -> MERGE into cex_reference (persistent catalog => write_accumulate,
    [[scraper-write-accumulate]]). Honest failure: an empty/failed pull is degraded, never a 0-row land."""
    import warehouse
    try:
        payloads = fetch(years)
    except Exception as e:
        log("cex_ref: BLS API unreachable (%s) — nothing landed [DEGRADED]" % type(e).__name__)
        return {"rows": 0, "uri": warehouse.uri("cex_reference"),
                "warnings": ["BLS fetch failed: %s" % e], "degraded": True}
    rows, warns = parse(payloads)
    rows, cwarns = _check(rows)
    warns += cwarns
    if not rows:
        log("cex_ref: 0 rows parsed — nothing landed [DEGRADED]")
        return {"rows": 0, "uri": warehouse.uri("cex_reference"), "warnings": warns, "degraded": True}
    res = warehouse.write_accumulate(
        "cex_reference", rows, fields=CEX_HEADER,
        key=lambda r: (r["dataset"], r["vintage_year"], r["item_code"], r["bracket_code"], r["metric_name"]))
    degraded = bool(warns)
    log("cex_reference: %d rows -> %s%s" % (res["rows"], res["uri"], "  [DEGRADED]" if degraded else ""))
    for w in warns:
        log("  warn: %s" % w)
    return {"rows": res["rows"], "uri": res["uri"], "warnings": warns, "degraded": degraded}


def query(item=None, bracket=None, year=None, limit=5000):
    """Read cex_reference by item mnemonic / bracket code / year (the /api/cex/spend helper)."""
    import warehouse
    where, params = ["1=1"], []
    for col, val in (("item_code", item), ("bracket_code", bracket), ("vintage_year", year)):
        if val not in (None, ""):
            where.append("%s = ?" % col); params.append(val)
    sql = "SELECT * FROM t WHERE " + " AND ".join(where) + " ORDER BY vintage_year, bracket_lo LIMIT %d" % int(limit)
    return warehouse.query("cex_reference", sql, params)


# ── CEX × ACS: trade-area demand dollars ─────────────────────────────────────────────────────────────
# ACS B19001 bracket metric (as landed by census_ref.pull_acs) -> {CEX bracket code: share}. The single
# straddle is ACS 60–75k across the CEX 70k boundary: 2/3 below, 1/3 above (uniform-within-bracket).
ACS_TO_CEX = {
    "hh_lt10k":     {"18": 1.0},
    "hh_10_15k":    {"18": 1.0},
    "hh_15_20k":    {"19": 1.0},
    "hh_20_25k":    {"19": 1.0},
    "hh_25_30k":    {"19": 1.0},
    "hh_30_35k":    {"07": 1.0},
    "hh_35_40k":    {"07": 1.0},
    "hh_40_45k":    {"08": 1.0},
    "hh_45_50k":    {"08": 1.0},
    "hh_50_60k":    {"09": 1.0},
    "hh_60_75k":    {"09": 2.0 / 3.0, "20": 1.0 / 3.0},
    "hh_75_100k":   {"20": 1.0},
    "hh_100_125k":  {"21": 1.0},
    "hh_125_150k":  {"21": 1.0},
    "hh_150_200k":  {"22": 1.0},
    "hh_200k_plus": {"23": 1.0},
}
_CAVEATS = [
    "CEX self-reported alcohol spend UNDERSTATES absolute $ (known survey underreport vs supplier-side "
    "volumes) — treat demand $ as a cross-geo COMPARATIVE index; the absolute figure is a floor",
    "CEX consumer unit ~ ACS household (close, not identical)",
    "CEX means are NATIONAL — no regional cut applied yet",
    "ACS 60-75k bracket split 2/3:1/3 across the CEX 70k boundary (uniform assumption)",
    "geo grain = the ACS geo pulled (county/state); ZIP/tract would sharpen",
]


def _cex_spend():
    """Latest complete CEX vintage -> {item: {bracket_code: mean $}}. None if not landed/complete."""
    rows = [r for r in query(limit=20000) if not r.get("suppressed")]
    for y in sorted({r["vintage_year"] for r in rows}, reverse=True):
        spend = {}
        for r in rows:
            if r["vintage_year"] == y:
                spend.setdefault(r["item_code"], {})[r["bracket_code"]] = float(r["metric_value"])
        if all(c in spend.get("ALCBEVG", {}) for c in BRACKETS if c != "all"):
            return y, spend
    return None, {}


def demand_for(geo_fips, geo_level=None):
    """Trade-area alcohol demand $ for one geo: ACS B19001 households × CEX mean spend, by bracket.
    Returns an honest {ok: False, reason} when either side isn't landed — never a guessed number."""
    import census_ref
    geo_fips = str(geo_fips)
    geo_level = geo_level or ("county" if len(geo_fips) == 5 else "state")
    acs = census_ref.query(dataset="acs", geo_level=geo_level, geo_fips=geo_fips, limit=200)
    metrics = {r["metric_name"]: float(r["metric_value"]) for r in acs
               if not r.get("suppressed") and r.get("metric_value") is not None}
    missing = [m for m in ACS_TO_CEX if m not in metrics]
    if missing:
        return {"ok": False, "geo_fips": geo_fips, "geo_level": geo_level,
                "reason": "ACS B19001 brackets not landed for this geo (%d missing, e.g. %s) — run the "
                          "census source with CENSUS_API_KEY (census_ref.build) to land them" % (len(missing), missing[0])}
    cex_year, spend = _cex_spend()
    if not cex_year:
        return {"ok": False, "geo_fips": geo_fips, "geo_level": geo_level,
                "reason": "cex_reference not landed/complete — run the cex source (cex_ref.build)"}
    hh = {}
    for acs_m, alloc in ACS_TO_CEX.items():
        for code, share in alloc.items():
            hh[code] = hh.get(code, 0.0) + metrics[acs_m] * share
    hh_total = sum(hh.values())
    out = {}
    for item in ("ALCBEVG", "ALCHOME", "ALCAWAY"):
        s = spend.get(item, {})
        if all(c in s for c in hh):
            out[item] = sum(hh[c] * s[c] for c in hh)
    if "ALCBEVG" not in out or not hh_total:
        return {"ok": False, "geo_fips": geo_fips, "geo_level": geo_level,
                "reason": "CEX bracket set incomplete for vintage %s" % cex_year}
    natl = spend.get("ALCBEVG", {}).get("all")
    per_hh = out["ALCBEVG"] / hh_total
    acs_vintage = next((r["vintage_year"] for r in acs if r["metric_name"] in ACS_TO_CEX), None)
    return {
        "ok": True, "geo_fips": geo_fips, "geo_level": geo_level,
        "acs_vintage": acs_vintage, "cex_vintage": cex_year, "households": round(hh_total),
        "demand_total_usd": round(out["ALCBEVG"]),
        "demand_at_home_usd": round(out.get("ALCHOME", 0)) or None,   # off-premise
        "demand_away_usd": round(out.get("ALCAWAY", 0)) or None,      # on-premise
        "demand_per_hh_usd": round(per_hh, 2),
        "demand_index_vs_us": round(100.0 * per_hh / natl, 1) if natl else None,
        "brackets": [{"bracket": BRACKETS[c][2], "households": round(hh[c]),
                      "mean_spend_usd": spend["ALCBEVG"][c],
                      "demand_usd": round(hh[c] * spend["ALCBEVG"][c])}
                     for c in sorted(hh, key=lambda c: BRACKETS[c][0])],
        "method": "ACS B19001 households x CEX LB02 mean alcohol $/CU, summed over brackets",
        "caveats": _CAVEATS,
    }


def build_demand(log=print):
    """Derive trade_area_demand for every geo with the full ACS bracket set. DERIVED REBUILD from two
    landed sources -> plain write_parquet is correct here (re-derivable, not an accumulating catalog)."""
    import warehouse
    cex_year, spend = _cex_spend()
    if not cex_year:
        log("trade_area_demand: cex_reference not landed — nothing derived [DEGRADED]")
        return {"rows": 0, "degraded": True}
    sql = ("SELECT geo_level, geo_fips, vintage_year, metric_name, metric_value FROM t "
           "WHERE dataset='acs' AND suppressed=false AND metric_name IN (%s)"
           % ",".join("'%s'" % m for m in ACS_TO_CEX))
    acs = warehouse.query("census_reference", sql)
    geos = {}
    for r in acs:
        geos.setdefault((r["geo_level"], r["geo_fips"], r["vintage_year"]), {})[r["metric_name"]] = float(r["metric_value"])
    ts, out = int(time.time()), []
    for (lvl, fips, vintage), metrics in sorted(geos.items()):
        if any(m not in metrics for m in ACS_TO_CEX):
            continue
        hh = {}
        for acs_m, alloc in ACS_TO_CEX.items():
            for code, share in alloc.items():
                hh[code] = hh.get(code, 0.0) + metrics[acs_m] * share
        hh_total = sum(hh.values())
        if not hh_total:
            continue
        tot = sum(hh[c] * spend["ALCBEVG"][c] for c in hh)
        home = sum(hh[c] * spend["ALCHOME"][c] for c in hh) if all(c in spend.get("ALCHOME", {}) for c in hh) else None
        away = sum(hh[c] * spend["ALCAWAY"][c] for c in hh) if all(c in spend.get("ALCAWAY", {}) for c in hh) else None
        natl = spend["ALCBEVG"].get("all")
        out.append(dict(geo_level=lvl, geo_fips=fips, acs_vintage=vintage, cex_vintage=cex_year,
                        households=round(hh_total), demand_total_usd=round(tot),
                        demand_at_home_usd=round(home) if home is not None else None,
                        demand_away_usd=round(away) if away is not None else None,
                        demand_per_hh_usd=round(tot / hh_total, 2),
                        demand_index_vs_us=round(100.0 * tot / hh_total / natl, 1) if natl else None,
                        method="ACS B19001 x CEX LB02 (see cex_ref.py)", computed_at=ts))
    if not out:
        log("trade_area_demand: no geo has the full ACS bracket set — run the census source first [DEGRADED]")
        return {"rows": 0, "degraded": True}
    res = warehouse.write_parquet("trade_area_demand", out)
    log("trade_area_demand: %d geos (ACS %s x CEX %d) -> %s"
        % (len(out), sorted({g[2] for g in geos}), cex_year, res["uri"]))
    return {"rows": len(out), "uri": res["uri"], "degraded": False}


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
        build_demand()
    elif "--demand" in sys.argv:
        fips = sys.argv[sys.argv.index("--demand") + 1]
        print(json.dumps(demand_for(fips), indent=2))
    else:
        ids = [s for s, _, _ in _all_series()]
        print("CEX series (%d, chunk=%d, key=%s):" % (len(ids), 50 if _key() else 25, "yes" if _key() else "keyless"))
        for s, item, code in _all_series():
            print("  %-22s %-24s %s" % (s, ITEMS[item], BRACKETS[code][2]))
        print("\nanchor: all-CU %s %d = $%.0f (published)" % (_ANCHOR["item"], _ANCHOR["year"], _ANCHOR["usd"]))
