"""tax_rates.py — beverage-alcohol TAX RATE reference layer (federal excise + state excise/sales).

The per-UNIT tax schedule the cost model stacks onto base cost — NOT collections (that's tax_revenue.py).
Long/tall, effective-dated, append-only: a re-pull REPLACES a
(level, jurisdiction, class, abv band, rate_type, effective_date) cell but never deletes prior
effective_dates, so rate HISTORY is preserved ([[append-only-versioned-master]]). landed_cost.py reads
it via current().

Two tiers, mirroring how the rest of the engine treats authority ([[cola-tiering]]):
  • FEDERAL (TTB) — the CBMA-permanent excise schedule: small, stable, high-confidence. Encoded here
    (FED_RATES) as the default, and optionally re-confirmed live from the TTB rate page. TTB is
    TLS-blocked from Fly, so live-refresh is a Mac-run step ([[ttb-fast-scrape]]); the encoded schedule
    is what Fly lands.
  • STATE (seed → DOR) — state excise + any special alcohol sales rate, seeded from tax_rates_seed.csv
    (provenance=seed, each row carries its source + as-of year), replaceable per-state by an authoritative
    DOR fetcher later. CONTROL states carry an IMPLIED excise — the state markup IS the tax — so their
    excise is flagged is_control_state and should be read from the control_state.py price book, not taken
    as a clean statutory rate ([[control-states-and-ca]]).

Honest failure: a class/state we can't map is emitted with provenance='unverified' + a warning, never a
silent 0 — a missing rate must look missing, not free. build() returns {rows, warnings, degraded}.

    python tax_rates.py            # smoke test: assemble + print sample rows + a landed-cost demo (no land)
    python tax_rates.py --build    # land to the warehouse (needs pyarrow / Tigris creds, e.g. on Fly)
"""
import csv, os, sys, time

RATE_HEADER = [
    "jurisdiction_level",   # federal | state | local
    "jurisdiction",         # US | 2-letter state | locality slug
    "beverage_class",       # spirits | wine | sparkling_wine | carbonated_wine | cider | beer | rtd | all
    "abv_min", "abv_max",   # ABV band the rate applies to ('' = unbounded); wine excise is ABV-tiered
    "rate_type",            # excise (per-volume specific) | sales (% of price) | markup (control-state)
    "basis",                # proof_gallon | wine_gallon | barrel | gallon | liter | percent
    "rate_value",           # float in `basis` units
    "currency",             # USD
    "is_control_state",     # bool — excise is the state markup, not a statutory per-gallon rate
    "effective_date",       # YYYY-MM-DD the rate took effect
    "source",               # TTB | <state DOR> | citation
    "provenance",           # verified | seed | unverified
    "pulled_at",            # epoch
    "notes",
]

# ── FEDERAL excise (TTB), CBMA rates — made permanent by the Taxpayer Certainty & Disaster Tax Relief
# Act of 2020 (in effect 2021+). STANDARD (non-small-producer) rates; the reduced-rate/credit tiers for
# small producers live in `notes` because a market-price model uses the standard rate unless it is
# pricing a specific small producer's volume. VERIFY the numbers on a live TTB run before trusting margins:
#   https://www.ttb.gov/tax-audit/tax-and-fee-rates
_FED_EFF = "2018-01-01"  # CBMA rates took effect 2018-01-01; permanent from 2021
FED_RATES = [
    # spirits — per PROOF gallon (proof gallon = wine gallon × proof/100; proof = 2×ABV)
    dict(beverage_class="spirits", abv_min="", abv_max="", basis="proof_gallon", rate_value=13.50,
         notes="CBMA reduced: $2.70 on first 100k proof gal; $13.34 on next up to 22.13M; $13.50 above"),
    # wine — per WINE gallon, ABV-tiered (>24% ABV is taxed as distilled spirits)
    dict(beverage_class="wine", abv_min=0.5, abv_max=16, basis="wine_gallon", rate_value=1.07,
         notes="still wine <=16% ABV; CBMA credit up to $1.00/gal on first 30k gal"),
    dict(beverage_class="wine", abv_min=16, abv_max=21, basis="wine_gallon", rate_value=1.57,
         notes="still wine 16-21% ABV"),
    dict(beverage_class="wine", abv_min=21, abv_max=24, basis="wine_gallon", rate_value=3.15,
         notes="still wine 21-24% ABV; >24% taxed as distilled spirits"),
    dict(beverage_class="sparkling_wine", abv_min="", abv_max=24, basis="wine_gallon", rate_value=3.40,
         notes="naturally sparkling wine"),
    dict(beverage_class="carbonated_wine", abv_min="", abv_max=24, basis="wine_gallon", rate_value=3.30,
         notes="artificially carbonated wine"),
    dict(beverage_class="cider", abv_min=0.5, abv_max=8.5, basis="wine_gallon", rate_value=0.226,
         notes="hard cider (apple/pear, within CBMA carbonation limits)"),
    # beer — per BARREL (31 US gallons)
    dict(beverage_class="beer", abv_min="", abv_max="", basis="barrel", rate_value=18.00,
         notes="$16/bbl on first 6M bbl; $3.50/bbl on first 60k bbl for domestic brewers <=2M bbl/yr"),
]


def _fed_rows(ts):
    """The encoded federal schedule → long/tall rows."""
    out = []
    for r in FED_RATES:
        row = {k: "" for k in RATE_HEADER}
        row.update(jurisdiction_level="federal", jurisdiction="US", rate_type="excise", currency="USD",
                   is_control_state=False, effective_date=_FED_EFF, source="TTB", provenance="verified",
                   pulled_at=ts)
        row.update(r)
        out.append(row)
    return out


def _seed_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "tax_rates_seed.csv")


def _state_rows(ts, log=print):
    """State excise / special-sales rows from the seed CSV. Each CSV row already carries its own source +
    effective_date + provenance; we only stamp pulled_at and normalize the shape. Missing file is a
    (degraded) warning, not a crash — federal still lands."""
    path, out, warns = _seed_path(), [], []
    if not os.path.exists(path):
        warns.append("state seed missing: %s (federal-only land)" % path)
        return out, warns
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if not (r.get("jurisdiction") or "").strip() or (r.get("jurisdiction") or "").startswith("#"):
                continue
            row = {k: "" for k in RATE_HEADER}
            for k in RATE_HEADER:
                if k in r and r[k] is not None:
                    row[k] = r[k]
            row["jurisdiction_level"] = row.get("jurisdiction_level") or "state"
            row["currency"] = row.get("currency") or "USD"
            row["provenance"] = row.get("provenance") or "seed"
            row["is_control_state"] = str(row.get("is_control_state")).strip().lower() in ("1", "true", "yes")
            row["pulled_at"] = ts
            if not str(row.get("rate_value") or "").strip():
                row["provenance"] = "unverified"
                warns.append("no rate_value for %s/%s/%s" % (row["jurisdiction"], row["beverage_class"], row["rate_type"]))
            out.append(row)
    log("tax_rates: %d state seed rows (%d unverified)" % (len(out), sum(1 for r in out if r["provenance"] == "unverified")))
    return out, warns


def refresh_federal(log=print):
    """Re-confirm FED_RATES against the live TTB rate page. TTB is TLS-blocked from Fly, so this only
    succeeds on a network-capable host (the Mac). Returns (ok, warnings) — it never overwrites the encoded
    schedule automatically; a mismatch is surfaced for a human to reconcile (rates change by legislation,
    not silently)."""
    import urllib.request
    url = "https://www.ttb.gov/tax-audit/tax-and-fee-rates"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HoodieUnifyd/1.0 (+tax reference)"})
        html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    except Exception as e:
        return False, ["TTB live-refresh unreachable (%s) — using encoded schedule" % type(e).__name__]
    warns = []
    for r in FED_RATES:
        needle = ("%.2f" % r["rate_value"]).rstrip("0").rstrip(".")
        if needle not in html and ("%.3f" % r["rate_value"]).rstrip("0") not in html:
            warns.append("TTB page no longer shows %s for %s — reconcile FED_RATES" % (needle, r["beverage_class"]))
    log("tax_rates: TTB live-refresh ok (%d discrepancies)" % len(warns))
    return True, warns


def assemble(log=print, live_federal=False):
    """Build the full long/tall row set (federal + state). Returns (rows, warnings)."""
    ts = int(time.time())
    warns = []
    if live_federal:
        _, w = refresh_federal(log=log); warns += w
    rows = _fed_rows(ts)
    srows, sw = _state_rows(ts, log=log)
    rows += srows; warns += sw
    return rows, warns


def build(log=print, live_federal=False):
    """Assemble + land to the warehouse (append-only, effective-dated). Returns {rows, warnings, degraded}."""
    import warehouse
    rows, warns = assemble(log=log, live_federal=live_federal)
    # key: a re-pull replaces the same rate cell but a new effective_date is a NEW row (history kept).
    def key(r):
        return (r["jurisdiction_level"], r["jurisdiction"], r["beverage_class"],
                str(r["abv_min"]), str(r["abv_max"]), r["rate_type"], r["effective_date"])
    res = warehouse.write_accumulate("tax_rates", rows, key=key, fields=RATE_HEADER)
    degraded = bool(warns)
    log("tax_rates: %d rows -> %s%s" % (res["rows"], res["uri"], "  [DEGRADED]" if degraded else ""))
    for w in warns:
        log("  warn: %s" % w)
    return {"rows": res["rows"], "uri": res["uri"], "warnings": warns, "degraded": degraded}


def query(jurisdiction=None, beverage_class=None, rate_type=None, level=None, limit=5000):
    """Read tax_rates by any combination (DuckDB against the Parquet)."""
    import warehouse
    where, params = ["1=1"], []
    for col, val in (("jurisdiction", jurisdiction), ("beverage_class", beverage_class),
                     ("rate_type", rate_type), ("jurisdiction_level", level)):
        if val not in (None, ""):
            where.append("%s = ?" % col); params.append(val)
    sql = "SELECT * FROM t WHERE " + " AND ".join(where) + " LIMIT %d" % int(limit)
    return warehouse.query("tax_rates", sql, params)


def _abv_ok(row, abv):
    """Does `abv` fall in this row's band? Empty bound = open. abv None passes bounded rows only if the
    band is unbounded (we cannot place an unknown ABV into a tier)."""
    lo, hi = str(row.get("abv_min") or "").strip(), str(row.get("abv_max") or "").strip()
    if not lo and not hi:
        return True
    if abv is None:
        return False
    if lo and float(abv) < float(lo):
        return False
    if hi and float(abv) > float(hi):
        return False
    return True


def current(rows, jurisdiction, beverage_class, rate_type="excise", abv=None, on_date=None):
    """Resolve the applicable rate from an already-fetched row list: the latest effective_date <= on_date
    whose ABV band contains `abv`. Returns the row dict or None. Kept pure (caller passes rows) so
    landed_cost can fetch once and resolve many."""
    cand = [r for r in rows
            if r.get("jurisdiction") == jurisdiction and r.get("beverage_class") == beverage_class
            and r.get("rate_type") == rate_type and _abv_ok(r, abv)
            and (not on_date or str(r.get("effective_date") or "") <= on_date)]
    if not cand:
        return None
    return sorted(cand, key=lambda r: str(r.get("effective_date") or ""))[-1]


if __name__ == "__main__":
    if "--build" in sys.argv:
        build(live_federal="--live" in sys.argv)
    else:
        rows, warns = assemble()
        print("assembled %d rows (%d warnings)" % (len(rows), len(warns)))
        print("\nFEDERAL:")
        for r in rows:
            if r["jurisdiction_level"] == "federal":
                print("  %-16s %5s-%-4s  %-12s $%-7s  %s" % (
                    r["beverage_class"], r["abv_min"], r["abv_max"], r["basis"], r["rate_value"], r["notes"][:44]))
        st = [r for r in rows if r["jurisdiction_level"] == "state"]
        print("\nSTATE seed: %d rows across %d jurisdictions" % (len(st), len({r["jurisdiction"] for r in st})))
        for r in st[:8]:
            print("  %-3s %-10s %-8s $%-6s /%-11s eff %s  [%s]" % (
                r["jurisdiction"], r["beverage_class"], r["rate_type"], r["rate_value"], r["basis"],
                r["effective_date"], r["provenance"]))
        for w in warns[:6]:
            print("  warn:", w)
