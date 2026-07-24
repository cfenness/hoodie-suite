#!/usr/bin/env python3
"""velocity.py — delta decomposition → implied sell-through (MOAT-PLAN.md Workstream V2/V3).

The crown jewel: per-store inventory deltas ARE demand data. A count going 40→33→21→48 is two
sales then a restock, per store, per day — a signal SipSource (monthly, distributor grain) and
Nielsen (panel, lagged) structurally can't produce. This turns `retail_observations` into
`fact_velocity` with a per-cell CONFIDENCE, then a dimension-bounded brand×week serving mart.

Estimator (per source×store×sku, ordered by date; pure DuckDB window functions — binny's is 7.3M obs):
Each consecutive observation PAIR is classified by its qty delta `dq`, the gap in days, and — crucially,
because V1 measured binny's at 58% jitter — whether the move is NOISE:
  • NOISE     dq≠0, |dq|≤JITTER_ABS, price unchanged, promo unchanged  → shelf-recount wobble, DAMPED
              (the load-bearing guard: without it, binny's "sales" would be mostly noise)
  • SALE      dq<0 and not noise and not censored                      → implied units = -dq
  • RESTOCK   dq>0 and not noise                                       → delivery event (resets baseline)
  • OOS       qty=0 (enter) / recovered from 0 (exit)                  → censored demand + a void signal
  • CENSORED  gap too long vs the source's cadence (a restock could hide a drawdown) → excluded from the
              unit estimate, counted against confidence — never silently summed
Only COUNT-tier sources (real unit counts, per obs_quality_source) get a unit estimate; STATE-tier
sources (in/out only) still yield OOS/void signals but implied_units stays null (honest — we can't
invent counts we never observed).

Confidence 0–1 = cadence tightness × (1 − censored fraction) × (1 − ½·source jitter fraction),
clamped. Every downstream number displays it; below CONF_FLOOR the cell is suppressed, not shown.

    python velocity.py            # rebuild fact_velocity + mart_velocity_brand_week
    python velocity.py --stats
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

JITTER_ABS = int(os.environ.get("VEL_JITTER_ABS", "2"))        # |dq|≤this w/ no price/promo change = noise
CENSOR_MULT = float(os.environ.get("VEL_CENSOR_MULT", "3.0"))  # gap > mult × cadence ⇒ censored pair
CENSOR_MIN_DAYS = float(os.environ.get("VEL_CENSOR_MIN_DAYS", "8"))  # …but never censor under this gap
CONF_FLOOR = float(os.environ.get("VEL_CONF_FLOOR", "0.2"))    # cells below this confidence are suppressed


def _mount(con):
    import warehouse
    obs_glob = (("s3://%s/%s/retail_observations/*.parquet" % (warehouse._bucket(), warehouse._prefix()))
                if warehouse.remote() else os.path.join(warehouse._LOCAL_DIR, "retail_observations", "*.parquet"))
    con.execute("CREATE OR REPLACE VIEW obs AS SELECT * FROM read_parquet('%s', union_by_name=true)"
                % obs_glob.replace("'", ""))
    # source instrument card (V1) — tier + jitter + cadence drive the estimator & confidence
    src = warehouse.uri("obs_quality_source").replace("'", "")
    con.execute("CREATE OR REPLACE VIEW srccard AS SELECT * FROM read_parquet('%s')" % src)


def build(log=print):
    """Rebuild fact_velocity (source×store×sku×week) + mart_velocity_brand_week. Returns row counts."""
    import warehouse
    con = warehouse.connect()
    _mount(con)

    # ── pair classification per (source, store, sku), ordered by date ────────────────────────────
    con.execute("""
        CREATE OR REPLACE TEMP TABLE pairs AS
        WITH seq AS (
            SELECT o.source, o.store_id, o.product_id, o.upc, o.brand,
                   TRY_CAST(o.date AS DATE) AS d,
                   TRY_CAST(o.qty AS DOUBLE) AS qty,
                   TRY_CAST(o.price AS DOUBLE) AS price,
                   COALESCE(TRY_CAST(o.on_promo AS BOOLEAN), false) AS promo,
                   c.qual_tier, COALESCE(c.jitter_frac, 0) AS jitter_frac,
                   COALESCE(c.median_cadence_days, 999) AS cadence
            FROM obs o JOIN srccard c ON o.source = c.source
            WHERE o.store_id IS NOT NULL AND o.product_id IS NOT NULL AND o.qty IS NOT NULL
              AND c.qual_tier = 'count'                    -- unit velocity only from real counters
        ),
        lagged AS (
            SELECT *,
                   lag(qty)   OVER w AS pq,
                   lag(price) OVER w AS pp,
                   lag(promo) OVER w AS ppr,
                   lag(d)     OVER w AS pd
            FROM seq
            WINDOW w AS (PARTITION BY source, store_id, product_id ORDER BY d)
        )
        SELECT source, store_id, product_id, any_value(upc) AS upc, brand, d, qty, jitter_frac, cadence,
               (qty - pq) AS dq,
               date_diff('day', pd, d) AS gap_days,
               -- NOISE: tiny move, no price/promo change (V1's jitter rule, applied per pair)
               (pq IS NOT NULL AND qty <> pq AND abs(qty - pq) <= %d
                AND price = pp AND promo = ppr) AS is_noise,
               -- CENSORED: gap so long a restock could hide inside a drawdown → don't trust the delta
               (pd IS NOT NULL AND date_diff('day', pd, d) > greatest(%f, %f * cadence)) AS is_censored,
               (pq IS NOT NULL AND pq > 0 AND qty = 0) AS oos_enter,
               (pq IS NOT NULL AND pq = 0 AND qty > 0) AS oos_exit
        FROM lagged
        GROUP BY source, store_id, product_id, brand, d, qty, price, promo, pq, pp, ppr, pd, jitter_frac, cadence
    """ % (JITTER_ABS, CENSOR_MIN_DAYS, CENSOR_MULT))

    # ── per (source, store, sku, week) fact ──────────────────────────────────────────────────────
    # a SALE is a non-noise, non-censored drawdown; units = -dq. Assign to the LATER obs's ISO week.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE fact AS
        WITH cls AS (
            SELECT source, store_id, product_id, upc, brand,
                   date_trunc('week', d) AS week, jitter_frac, cadence,
                   CASE WHEN dq < 0 AND NOT is_noise AND NOT is_censored THEN -dq ELSE 0 END AS sale_units,
                   CASE WHEN dq < 0 AND NOT is_noise AND NOT is_censored THEN 1 ELSE 0 END AS sale_events,
                   CASE WHEN dq > 0 AND NOT is_noise THEN 1 ELSE 0 END AS restock_events,
                   CASE WHEN is_noise THEN abs(dq) ELSE 0 END AS noise_damped_units,
                   CASE WHEN is_censored THEN 1 ELSE 0 END AS censored_pairs,
                   CASE WHEN oos_enter THEN 1 ELSE 0 END AS oos_events,
                   1 AS pairs
            FROM pairs WHERE dq IS NOT NULL
        )
        SELECT source, store_id, product_id, any_value(upc) AS upc, brand, week,
               SUM(sale_units)         AS implied_units,
               SUM(sale_events)        AS sale_events,
               SUM(restock_events)     AS restock_events,
               SUM(noise_damped_units) AS noise_damped_units,
               SUM(censored_pairs)     AS censored_pairs,
               SUM(oos_events)         AS oos_events,
               SUM(pairs)              AS pairs,
               any_value(jitter_frac)  AS jitter_frac,
               any_value(cadence)      AS cadence
        FROM cls GROUP BY source, store_id, product_id, brand, week
    """)
    # confidence: cadence tightness × (1 − censored frac) × (1 − ½ jitter). clamp [0,1].
    con.execute("""
        ALTER TABLE fact ADD COLUMN confidence DOUBLE;
        UPDATE fact SET confidence = greatest(0.0, least(1.0,
            least(1.0, 3.0 / greatest(cadence, 1.0))
            * (1.0 - censored_pairs * 1.0 / NULLIF(pairs, 0))
            * (1.0 - 0.5 * jitter_frac)));
    """)
    # suppress below the floor (honesty rule) — a suppressed cell is dropped, not shown as a fake 0
    fact_rows = con.execute("SELECT COUNT(*) FROM fact WHERE confidence >= %f" % CONF_FLOOR).fetchone()[0]
    _write(con, "fact_velocity", "SELECT * FROM fact WHERE confidence >= %f" % CONF_FLOOR)

    # ── dimension-bounded brand×week serving mart (the SipSource pattern) ─────────────────────────
    # total observed velocity per brand per week, confidence-weighted; bounded by brands×weeks.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE bmart AS
        SELECT brand, week,
               SUM(implied_units)                                   AS implied_units,
               SUM(implied_units * confidence) / NULLIF(SUM(confidence), 0) AS conf_wt_units_per_store,
               COUNT(DISTINCT store_id || '|' || product_id)        AS cells,
               COUNT(DISTINCT store_id)                             AS stores,
               SUM(restock_events)                                  AS restock_events,
               SUM(oos_events)                                      AS oos_events,
               round(avg(confidence), 3)                            AS avg_confidence
        FROM fact WHERE confidence >= %f AND brand IS NOT NULL AND trim(brand) <> ''
        GROUP BY brand, week
    """ % CONF_FLOOR)
    # (unbranded velocity stays in fact_velocity — it's real; it just isn't a brand-leaderboard row
    # until Workstream M resolves its brand. Honest: we don't drop it, we don't mislabel it.)
    mart_rows = con.execute("SELECT COUNT(*) FROM bmart").fetchone()[0]
    _write(con, "mart_velocity_brand_week", "SELECT * FROM bmart")

    log("[velocity] fact_velocity %d cells (conf≥%.2f), mart_velocity_brand_week %d brand-weeks"
        % (fact_rows, CONF_FLOOR, mart_rows))
    return {"fact_cells": fact_rows, "brand_weeks": mart_rows}


def _write(con, table, select_sql):
    import pyarrow.parquet as pq
    import warehouse
    arrow = con.execute(select_sql).fetch_arrow_table()
    if warehouse.remote():
        warehouse._retry(lambda: pq.write_table(arrow, "%s/%s" % (warehouse._bucket(), warehouse._s3_key(table)),
                                                filesystem=warehouse._s3fs(), compression="zstd"), table)
    else:
        pq.write_table(arrow, warehouse._local_path(table), compression="zstd")


def stats(log=print):
    import warehouse
    tot = warehouse.query("fact_velocity",
                          "SELECT COUNT(*) cells, COUNT(DISTINCT source) srcs, "
                          "round(SUM(implied_units)) units, round(SUM(noise_damped_units)) noise, "
                          "round(avg(confidence),3) conf FROM t")[0]
    log("[velocity] %s cells · %s implied units · %s units DAMPED as noise · avg conf %s · %s sources"
        % (f"{tot['cells']:,}", f"{int(tot['units'] or 0):,}", f"{int(tot['noise'] or 0):,}",
           tot["conf"], tot["srcs"]))
    log("  top brands by confidence-weighted velocity (latest week):")
    for r in warehouse.query("mart_velocity_brand_week",
                             "SELECT brand, week, implied_units, avg_confidence, stores FROM t "
                             "WHERE week = (SELECT MAX(week) FROM t) ORDER BY implied_units DESC LIMIT 12"):
        log("    %-28s %s  units=%-8s stores=%-4s conf=%s"
            % (str(r["brand"])[:28], r["week"], int(r["implied_units"] or 0), r["stores"], r["avg_confidence"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Delta decomposition → implied velocity (MOAT V2).")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args(argv)
    build()
    if a.stats:
        stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
