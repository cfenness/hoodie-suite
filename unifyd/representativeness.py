#!/usr/bin/env python3
"""representativeness.py — from "what we OBSERVED" to "what the MARKET did" (MOAT-PLAN.md Workstream R).

The bridge that turns an observation engine into a market-truth engine — honestly. We never pretend a
scrape is a census; we MEASURE how far it is from one and project with stated uncertainty.

  R1 COVERAGE    per market cell (state × channel): observed outlets ÷ the known universe
                 (outlet_master). The ugly cells included — especially the ugly cells.
  R2 PROJECTION  every market metric ships in TWO labelled flavours:
                   OBSERVED  — deterministic. The sum of what we actually saw. Always shown.
                   PROJECTED — inference. Observed per-store mean × the universe, with a CI from the
                              finite-population survey estimator (Var = N²·(s²/n)·FPC). LABELLED, never
                              blended with OBSERVED (the DETERMINISTIC-vs-INFERENCE doctrine, extended
                              to statistics).
  R3 SUPPRESS    below MIN_OBS stores or MIN_COVERAGE, PROJECTED is withheld with the reason shown —
                 a blank that says why beats a number that lies.

Reuses the velocity brand×store data (fact_velocity) as the metric to project and `outlet_master` as
the universe; source→state from the same map the calibrator uses. R4 (anchor validation of the
projection) reuses velocity_calibrate's spine — one validation loop, two consumers.

    python representativeness.py            # build coverage_cells + market_projection
    python representativeness.py --stats
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# source → state (the sources that produce velocity we can place on a market). Extend as the footprint
# grows; an unmapped source is excluded from projection (honest — we won't guess a market).
SOURCE_STATE = {"binnys": "IL"}
MIN_OBS = int(os.environ.get("REP_MIN_OBS", "8"))          # need ≥ this many observed stores to project
MIN_COVERAGE = float(os.environ.get("REP_MIN_COVERAGE", "0.02"))  # …and ≥ this coverage of the universe
Z = 1.96                                                    # 95% CI


def _universe_by_state():
    """Known outlet universe per state, from outlet_master (the denominator). Blank state dropped."""
    import warehouse
    try:
        rows = warehouse.query("outlet_master",
                               "SELECT state, COUNT(*) n FROM t WHERE state IS NOT NULL AND trim(state) <> '' "
                               "GROUP BY state")
        return {r["state"]: int(r["n"]) for r in rows}
    except Exception:
        return {}


def build(log=print):
    import warehouse
    con = warehouse.connect()
    fv = warehouse.uri("fact_velocity").replace("'", "")
    con.execute("CREATE OR REPLACE VIEW fv AS SELECT * FROM read_parquet('%s')" % fv)
    universe = _universe_by_state()

    # per (state, brand) observed stats over the stores we actually saw
    cases = " ".join("WHEN '%s' THEN '%s'" % (s, st) for s, st in SOURCE_STATE.items())
    con.execute("""
        CREATE OR REPLACE TEMP TABLE cell AS
        WITH placed AS (
            SELECT CASE source %s ELSE NULL END AS state, brand, store_id,
                   SUM(implied_units) AS store_units
            FROM fv WHERE brand IS NOT NULL AND trim(brand) <> ''
            GROUP BY 1, 2, 3
        )
        SELECT state, brand,
               COUNT(DISTINCT store_id)      AS obs_stores,
               round(SUM(store_units))       AS observed_units,
               avg(store_units)              AS mean_per_store,
               COALESCE(stddev_samp(store_units), 0) AS sd_per_store
        FROM placed WHERE state IS NOT NULL
        GROUP BY state, brand
    """ % cases)
    cells = con.execute("SELECT * FROM cell").fetch_arrow_table().to_pylist()

    proj = []
    for c in cells:
        st, N = c["state"], universe.get(c["state"], 0)
        n = int(c["obs_stores"] or 0)
        xbar = float(c["mean_per_store"] or 0)
        sd = float(c["sd_per_store"] or 0)
        coverage = (n / N) if N else None
        row = dict(state=st, brand=c["brand"], universe_outlets=N, obs_stores=n,
                   coverage=round(coverage, 4) if coverage is not None else None,
                   observed_units=int(c["observed_units"] or 0))
        # PROJECTED only when the cell is dense enough to trust; else suppressed with the reason.
        if N and n >= MIN_OBS and coverage is not None and coverage >= MIN_COVERAGE:
            fpc = math.sqrt(max((N - n) / (N - 1), 0.0)) if N > 1 else 0.0
            se_total = N * (sd / math.sqrt(n)) * fpc if n > 0 else 0.0
            projected = N * xbar
            row.update(projected_units=round(projected), ci_low=round(max(projected - Z * se_total, 0)),
                       ci_high=round(projected + Z * se_total),
                       ci_pct=round(Z * se_total / projected * 100, 1) if projected else None,
                       projected_status="projected")
        else:
            reasons = []
            if not N:
                reasons.append("no universe for state")
            if n < MIN_OBS:
                reasons.append("only %d obs stores (<%d)" % (n, MIN_OBS))
            if coverage is not None and coverage < MIN_COVERAGE:
                reasons.append("coverage %.2f%% (<%.0f%%)" % (coverage * 100, MIN_COVERAGE * 100))
            row.update(projected_units=None, ci_low=None, ci_high=None, ci_pct=None,
                       projected_status="suppressed: " + "; ".join(reasons))
        proj.append(row)

    pf = ["state", "brand", "universe_outlets", "obs_stores", "coverage", "observed_units",
          "projected_units", "ci_low", "ci_high", "ci_pct", "projected_status"]
    warehouse.write_parquet("market_projection", proj, fields=pf, allow_empty=True)

    # coverage_cells: the state-level coverage roll-up (how much of each state's universe we observe)
    cov = {}
    for c in cells:
        st = c["state"]
        cov.setdefault(st, set())
    con2 = warehouse.connect()
    con2.execute("CREATE OR REPLACE VIEW fv AS SELECT * FROM read_parquet('%s')" % fv)
    obs_stores = con2.execute("""
        SELECT CASE source %s ELSE NULL END AS state, COUNT(DISTINCT store_id) obs
        FROM fv GROUP BY 1
    """ % cases).fetch_arrow_table().to_pylist()
    ccells = []
    for r in obs_stores:
        st = r["state"]
        if not st:
            continue
        N = universe.get(st, 0)
        obs = int(r["obs"] or 0)
        ccells.append(dict(state=st, universe_outlets=N, observed_outlets=obs,
                           coverage=round(obs / N, 4) if N else None,
                           brands_observed=sum(1 for c in cells if c["state"] == st)))
    warehouse.write_parquet("coverage_cells", ccells,
                            fields=["state", "universe_outlets", "observed_outlets", "coverage", "brands_observed"],
                            allow_empty=True)

    n_proj = sum(1 for p in proj if p["projected_status"] == "projected")
    log("[representativeness] %d brand×state cells (%d projected, %d suppressed) · %d coverage cells"
        % (len(proj), n_proj, len(proj) - n_proj, len(ccells)))
    return {"cells": len(proj), "projected": n_proj, "coverage_cells": len(ccells)}


def stats(log=print):
    import warehouse
    log("[coverage] observed share of each state's universe:")
    for r in warehouse.query("coverage_cells", "SELECT * FROM t ORDER BY observed_outlets DESC"):
        log("  %-4s %s/%s outlets = %s%% coverage · %s brands"
            % (r["state"], r["observed_outlets"], f"{r['universe_outlets']:,}",
               round((r["coverage"] or 0) * 100, 2), r["brands_observed"]))
    log("[projection] OBSERVED vs PROJECTED (top brands where projectable):")
    got = warehouse.query("market_projection",
                          "SELECT state, brand, observed_units, projected_units, ci_pct, coverage "
                          "FROM t WHERE projected_status='projected' ORDER BY projected_units DESC LIMIT 8")
    for r in got:
        log("  %-4s %-22s OBSERVED %s → PROJECTED %s ±%s%% (cov %s%%)"
            % (r["state"], str(r["brand"])[:22], f"{r['observed_units']:,}", f"{int(r['projected_units']):,}",
               r["ci_pct"], round((r["coverage"] or 0) * 100, 2)))
    supp = warehouse.query("market_projection", "SELECT COUNT(*) n FROM t WHERE projected_status<>'projected'")[0]["n"]
    log("  (%s cells OBSERVED-only — projection suppressed, honest: coverage/obs too thin)" % f"{supp:,}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Representativeness: coverage + OBSERVED/PROJECTED market metrics (MOAT R).")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args(argv)
    build()
    if a.stats:
        stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
