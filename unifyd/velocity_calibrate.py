#!/usr/bin/env python3
"""velocity_calibrate.py — prove the velocity engine is RIGHT, not just plausible (MOAT-PLAN V4).

Two validators, honest about what each can do TODAY:

1. CONSERVATION (internal, runs now, needs no external data). In steady state a store re-stocks what
   it sells, so across a stable population implied SALES ≈ implied RESTOCK. The ratio is a calibration
   signal with a direction: ratio ≫ 1 ⇒ we're OVER-counting (recount noise leaking through as sales);
   ratio ≪ 1 ⇒ UNDER-counting (censoring/OOS eating real drawdowns). This is the accuracy number we
   can publish immediately, per source, and it directly critiques the estimator's own settings.

2. EXTERNAL MAPE (the scoreboard headline — needs an overlapping footprint). Joins velocity rolled to
   brand×market×period against a ground-truth actuals adapter, fits a single scale factor (least
   squares through the origin — velocity is a proportional proxy, not absolute), and reports MAPE plus
   COVERAGE (how many cells actually overlapped). It reports coverage HONESTLY: today the velocity
   footprint (binny's IL, sevennow TX) does not overlap the available anchors (Montgomery County MD
   sales; OR/UT/NC are price lists, not volume; Iowa BigQuery not yet landed), so coverage=0 and the
   harness says so rather than inventing a number. The moment a velocity source lands in an anchor
   market — or Iowa is pulled — this produces the real MAPE with no code change.

Ground-truth adapters (add one row to ANCHORS to wire a new anchor):
  mont_sales  Montgomery County MD DLC — real monthly retail unit sales (rtl_sales) by item×month.
  (iowa)      Iowa BigQuery — every Class-E spirits transaction; store-level, current. Land via iowa_bq.

    python velocity_calibrate.py                 # conservation + attempt every anchor
    python velocity_calibrate.py --anchor mont_sales --stats
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# source → market (state). binny's is Chicagoland (IL); sevennow store labels carry a state; extend as
# velocity-producing sources grow. Unmapped sources are excluded from the MARKET rollup (honest — we
# won't guess a market we can't attribute).
SOURCE_STATE = {"binnys": "IL"}


def _brand_norm_sql(col):
    """Cheap brand normalizer for the join key — upper, strip, collapse whitespace. Full brand
    resolution is Workstream M's job; this is a best-effort bridge so a real anchor can join at all."""
    return "upper(trim(regexp_replace(%s, '\\s+', ' ', 'g')))" % col


# Ground-truth adapters: each returns a SELECT yielding (brand_key, market, period 'YYYY-MM', actual_units).
ANCHORS = {
    # Montgomery County MD monthly retail unit sales. No brand column → derive a coarse brand key from
    # the leading token(s) of item_description (best-effort; the honest limitation is noted in coverage).
    "mont_sales": dict(
        market="MD",
        sql=lambda: (
            "SELECT %s AS brand_key, 'MD' AS market, "
            "printf('%%04d-%%02d', TRY_CAST(calendar_year AS INT), TRY_CAST(cal_month_num AS INT)) AS period, "
            "SUM(TRY_CAST(rtl_sales AS DOUBLE)) AS actual_units "
            "FROM t WHERE rtl_sales IS NOT NULL "
            "GROUP BY 1,2,3" % _brand_norm_sql("split_part(item_description, ' ', 1)")),
    ),
}


def conservation(log=print):
    """Internal accuracy signal: implied SALES vs implied RESTOCK per source (steady-state balance)."""
    import warehouse
    rows = warehouse.query(
        "fact_velocity",
        "SELECT source, round(SUM(implied_units)) sales, round(SUM(restock_units)) restock, "
        "round(SUM(implied_units) / NULLIF(SUM(restock_units),0), 3) ratio, "
        "round(SUM(noise_damped_units)) noise_damped, COUNT(*) cells "
        "FROM t GROUP BY source ORDER BY sales DESC")
    tot = warehouse.query(
        "fact_velocity",
        "SELECT round(SUM(implied_units)) sales, round(SUM(restock_units)) restock, "
        "round(SUM(implied_units)/NULLIF(SUM(restock_units),0),3) ratio FROM t")[0]
    log("[conservation] steady-state balance (implied SALES vs RESTOCK; ratio→1 is well-calibrated):")
    log("  %-12s %12s %12s %8s %12s" % ("source", "sales", "restock", "ratio", "noise_damp"))
    for r in rows:
        flag = "" if r["ratio"] and 0.7 <= r["ratio"] <= 1.4 else "  ← over-count" if (r["ratio"] or 0) > 1.4 else "  ← under-count"
        log("  %-12s %12s %12s %8s %12s%s" % (r["source"], f"{int(r['sales'] or 0):,}",
            f"{int(r['restock'] or 0):,}", r["ratio"], f"{int(r['noise_damped'] or 0):,}", flag))
    log("  TOTAL sales=%s restock=%s ratio=%s" % (f"{int(tot['sales'] or 0):,}",
        f"{int(tot['restock'] or 0):,}", tot["ratio"]))
    return {"by_source": rows, "total": tot}


def _velocity_market():
    """Velocity rolled to (brand_key, market, period 'YYYY-MM') for sources with a known market."""
    import warehouse
    cases = " ".join("WHEN '%s' THEN '%s'" % (s, st) for s, st in SOURCE_STATE.items())
    return warehouse.query(
        "fact_velocity",
        "SELECT %s AS brand_key, CASE source %s ELSE NULL END AS market, "
        "strftime(TRY_CAST(week AS DATE), '%%Y-%%m') AS period, SUM(implied_units) AS implied_units "
        "FROM t WHERE brand IS NOT NULL AND trim(brand) <> '' "
        "GROUP BY 1,2,3 HAVING market IS NOT NULL" % (_brand_norm_sql("brand"), cases))


def calibrate(anchor, log=print):
    """Join velocity↔anchor at brand×market×period, fit a scale, report MAPE + coverage. Honest:
    coverage=0 (no footprint overlap) is reported as pending, never as a fabricated accuracy."""
    import warehouse
    spec = ANCHORS.get(anchor)
    if not spec:
        log("[calibrate] no adapter for %r" % anchor)
        return {"anchor": anchor, "error": "no adapter"}
    try:
        actual = warehouse.query(anchor, spec["sql"]())
    except Exception as e:
        log("[calibrate] anchor %s unreadable: %s" % (anchor, str(e)[:80]))
        return {"anchor": anchor, "error": str(e)[:120], "coverage": 0}
    vel = {(r["brand_key"], r["market"], r["period"]): r["implied_units"] for r in _velocity_market()}
    pairs = []
    for a in actual:
        k = (a["brand_key"], a["market"], a["period"])
        if k in vel and (a["actual_units"] or 0) > 0 and (vel[k] or 0) > 0:
            pairs.append((vel[k], a["actual_units"]))
    if not pairs:
        vmarkets = sorted({m for (_, m, _) in vel})
        log("[calibrate] anchor=%s market=%s: 0 overlapping brand×market×period cells "
            "(velocity markets present: %s). MAPE PENDING an overlapping footprint — not fabricating a number."
            % (anchor, spec["market"], vmarkets or "none"))
        return {"anchor": anchor, "coverage": 0, "velocity_markets": vmarkets, "status": "pending-overlap"}
    # scale = Σactual / Σimplied (least squares through origin), then MAPE of scaled implied vs actual
    si = sum(v for v, _ in pairs)
    sa = sum(a for _, a in pairs)
    scale = sa / si if si else 0
    mape = sum(abs(scale * v - a) / a for v, a in pairs) / len(pairs) * 100
    log("[calibrate] anchor=%s: %d overlapping cells · scale=%.3f · MAPE=%.1f%%" % (anchor, len(pairs), scale, mape))
    return {"anchor": anchor, "coverage": len(pairs), "scale": round(scale, 4), "mape_pct": round(mape, 1)}


def build(log=print):
    """Land the calibration result (conservation + every anchor attempt) as `velocity_calibration`."""
    import warehouse
    cons = conservation(log=log)
    results = [dict(kind="conservation", anchor="", source=r["source"], coverage=r["cells"],
                    metric="sales_restock_ratio", value=r["ratio"]) for r in cons["by_source"]]
    for anchor in ANCHORS:
        c = calibrate(anchor, log=log)
        results.append(dict(kind="external_mape", anchor=anchor, source="",
                            coverage=c.get("coverage", 0), metric="mape_pct", value=c.get("mape_pct")))
    warehouse.write_parquet("velocity_calibration", results,
                            fields=["kind", "anchor", "source", "coverage", "metric", "value"],
                            allow_empty=True)
    log("[velocity_calibrate] %d rows -> velocity_calibration" % len(results))
    return {"rows": len(results)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Calibrate velocity vs ground truth (MOAT V4).")
    ap.add_argument("--anchor", default=None, help="calibrate one anchor (default: all + conservation)")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args(argv)
    if a.anchor:
        conservation()
        calibrate(a.anchor)
    else:
        build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
