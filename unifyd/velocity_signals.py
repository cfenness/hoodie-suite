#!/usr/bin/env python3
"""velocity_signals.py — the salable output on top of velocity (MOAT-PLAN.md Workstream V5).

What a sales team actually buys, derived from fact_velocity + its confidence:

  signal_movers  brand acceleration/deceleration, week-over-week. MATCHED-CELL (same-store-sales)
                 method: only cells (store×sku) observed in BOTH weeks count — so a partial
                 observation week can't manufacture a fake crash (weeks 1 & 3 in the current data are
                 partial; raw-unit WoW would lie, matched-cell doesn't). Confidence-gated.
  signal_voids   distribution OPPORTUNITIES, framed as the win + the next move (positive-framing
                 doctrine): a brand went out of stock at N stores → estimated lost units = the
                 brand's own typical per-store velocity × the out cells. Every void cites its evidence.

Both are dimension-small (brand grain), land to the warehouse, and carry confidence + as_of so the
surface can prove what it shows. Restock-cadence (order-timing) needs the finer restock-timing grain
than the weekly fact carries — deferred to a follow-up that reads the pair stream directly.

    python velocity_signals.py            # rebuild signal_movers + signal_voids
    python velocity_signals.py --stats
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MIN_CELLS = int(os.environ.get("SIG_MIN_CELLS", "5"))        # a mover needs ≥ this many matched cells
MIN_PREV_UNITS = float(os.environ.get("SIG_MIN_PREV_UNITS", "10"))  # …and a non-trivial prior base
CONF_FLOOR = float(os.environ.get("SIG_CONF_FLOOR", "0.4"))  # only reasonably-trusted cells feed signals
FULL_FRAC = float(os.environ.get("SIG_FULL_FRAC", "0.8"))    # a week is "full" if its observed-days ≥
#   this × the best-observed week. A WoW mover is CONFIRMED only when BOTH weeks are full — else the
#   partial one (a start/current week) skews the % in EITHER direction (fake -100% AND fake +7730%).


def _fv(con):
    import warehouse
    src = warehouse.uri("fact_velocity").replace("'", "")
    con.execute("CREATE OR REPLACE VIEW fv AS SELECT * FROM read_parquet('%s') WHERE confidence >= %f "
                "AND brand IS NOT NULL AND trim(brand) <> ''" % (src, CONF_FLOOR))
    # per-week OBSERVATION coverage (distinct calendar dates seen that week). Matched-cell WoW is
    # coverage-proof to cells appearing/disappearing, but NOT to a week observed for fewer DAYS than
    # its neighbor (a current/in-progress week undercounts sales → fake declines). We flag, not drop.
    # ONE ACCESSOR. retail_observations is a DIRECTORY of parts, and handing DuckDB
    # `<dir>/*.parquet` corrupts the read at this scale — measured on this exact table, 3,824
    # partitions / 51.7M rows, reproduced 4/4 (see warehouse.query_parts). attach_view resolves the
    # file list and passes it explicitly, for every layout.
    #
    # This site mattered most of the four: the read below sits inside a try/except, so a corrupt read
    # here degraded QUIETLY — wcov simply missing, coverage unflagged — rather than failing loudly
    # like the other three.
    warehouse.attach_view(con, "retail_observations", view="_ro")
    try:
        # coverage measured ONLY over the sources that FEED velocity (count-tier) — a non-velocity source
        # scraping on a given day must not inflate that week's apparent completeness for the movers.
        con.execute("CREATE OR REPLACE VIEW wcov AS SELECT date_trunc('week', TRY_CAST(date AS DATE)) AS week, "
                    "COUNT(DISTINCT date) AS obs_days FROM _ro "
                    "WHERE source IN (SELECT DISTINCT source FROM fv) GROUP BY 1")
    except Exception:
        con.execute("CREATE OR REPLACE VIEW wcov AS SELECT CAST(NULL AS DATE) AS week, 0 AS obs_days WHERE false")


def build(log=print):
    import warehouse
    con = warehouse.connect()
    _fv(con)

    # ── MOVERS: matched-cell week-over-week per brand ────────────────────────────────────────────
    # cell = store_id|product_id. For each brand and each consecutive (prev_week → week) pair, sum
    # implied_units over ONLY the cells seen in both weeks — the same-store method, coverage-proof.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE movers AS
        WITH cell AS (
            SELECT brand, store_id || '|' || product_id AS cell, week,
                   SUM(implied_units) AS units, avg(confidence) AS conf
            FROM fv GROUP BY brand, cell, week
        ),
        wk AS (SELECT DISTINCT week FROM cell),
        ordered AS (SELECT week, row_number() OVER (ORDER BY week) AS wn FROM wk),
        pairs AS (
            SELECT a.week AS week, b.week AS prev_week
            FROM ordered a JOIN ordered b ON a.wn = b.wn + 1
        ),
        matched AS (
            SELECT p.week, p.prev_week, c.brand, c.cell,
                   cur.units AS cur_units, prv.units AS prev_units, cur.conf AS conf
            FROM pairs p
            JOIN cell cur ON cur.week = p.week
            JOIN cell prv ON prv.week = p.prev_week AND prv.brand = cur.brand AND prv.cell = cur.cell
            JOIN cell c   ON c.week = p.week AND c.brand = cur.brand AND c.cell = cur.cell
        ),
        agg AS (
            SELECT brand, week, prev_week,
                   COUNT(DISTINCT cell)                       AS matched_cells,
                   round(SUM(cur_units))                      AS units,
                   round(SUM(prev_units))                     AS prev_units,
                   round(SUM(cur_units) - SUM(prev_units))    AS delta_units,
                   round((SUM(cur_units) - SUM(prev_units)) / NULLIF(SUM(prev_units), 0) * 100, 1) AS pct_change,
                   round(avg(conf), 3)                        AS confidence
            FROM matched
            GROUP BY brand, week, prev_week
            HAVING COUNT(DISTINCT cell) >= %d AND SUM(prev_units) >= %f
        )
        SELECT a.*,
               -- PARTIAL: a week observed on materially fewer calendar days than a full week under-counts
               -- its sales. A mover is CONFIRMED only when BOTH weeks are full — a partial base OR a
               -- partial current week skews the WoW pct in either direction (fake -100pct AND fake +7730pct).
               (COALESCE(cw.obs_days, 0) < %f * (SELECT MAX(obs_days) FROM wcov)
                OR COALESCE(pw.obs_days, 0) < %f * (SELECT MAX(obs_days) FROM wcov)) AS partial,
               COALESCE(cw.obs_days, 0) AS cur_obs_days, COALESCE(pw.obs_days, 0) AS prev_obs_days
        FROM agg a LEFT JOIN wcov cw ON cw.week = a.week LEFT JOIN wcov pw ON pw.week = a.prev_week
    """ % (MIN_CELLS, MIN_PREV_UNITS, FULL_FRAC, FULL_FRAC))
    mv_rows = con.execute("SELECT COUNT(*) FROM movers").fetchone()[0]
    _write(con, "signal_movers", "SELECT * FROM movers")

    # ── VOIDS: OOS opportunities, framed as the win + estimated recoverable units ─────────────────
    # a brand's typical per-store weekly velocity (its own baseline) × the number of store-cells that
    # went OOS = estimated recoverable units if the void were closed. Positive framing: opportunity.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE voids AS
        WITH base AS (   -- brand's typical per-cell weekly velocity (only cells that actually sold)
            SELECT brand, avg(implied_units) AS units_per_cell
            FROM fv WHERE implied_units > 0 GROUP BY brand
        ),
        oos AS (
            SELECT brand,
                   COUNT(DISTINCT store_id) FILTER (WHERE oos_events > 0)       AS oos_stores,
                   SUM(oos_events)                                             AS oos_events,
                   COUNT(DISTINCT store_id)                                    AS stores_seen,
                   avg(confidence)                                             AS conf
            FROM fv GROUP BY brand
        )
        SELECT o.brand, o.oos_stores, o.stores_seen, o.oos_events,
               round(b.units_per_cell, 1)                                      AS typ_units_per_store_wk,
               round(o.oos_stores * b.units_per_cell)                          AS est_recoverable_units_wk,
               round(o.oos_stores * 100.0 / NULLIF(o.stores_seen, 0), 1)       AS pct_stores_out,
               round(o.conf, 3)                                                AS confidence
        FROM oos o JOIN base b USING (brand)
        WHERE o.oos_stores > 0
    """)
    vd_rows = con.execute("SELECT COUNT(*) FROM voids").fetchone()[0]
    _write(con, "signal_voids", "SELECT * FROM voids")

    log("[velocity_signals] signal_movers %d brand-weeks, signal_voids %d brand opportunities"
        % (mv_rows, vd_rows))
    return {"movers": mv_rows, "voids": vd_rows}


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
    # authoritative movers = the latest NON-PARTIAL week (both weeks observed on comparable days)
    wk = warehouse.query("signal_movers", "SELECT MAX(week) w FROM t WHERE NOT partial")
    confw = wk[0]["w"] if wk and wk[0]["w"] else None
    if not confw:
        np = warehouse.query("signal_movers", "SELECT COUNT(*) n FROM t")[0]["n"]
        log("[movers] NO confirmed movers yet — need two consecutive FULL observation weeks "
            "(all %d mover rows involve a partial start/current week; the honest answer is 'not yet')." % np)
        confw = "—"
    log("[movers] accelerating brands (latest CONFIRMED week %s, matched-cell WoW):" % confw)
    for r in warehouse.query("signal_movers",
                             "SELECT brand, week, units, prev_units, pct_change, matched_cells, confidence "
                             "FROM t WHERE NOT partial AND week = (SELECT MAX(week) FROM t WHERE NOT partial) "
                             "AND pct_change > 0 ORDER BY pct_change DESC LIMIT 8"):
        log("    ↑ %-24s %s  %+g%% (%s→%s u · %s cells · conf %s)"
            % (str(r["brand"])[:24], r["week"], r["pct_change"], int(r["prev_units"]), int(r["units"]),
               r["matched_cells"], r["confidence"]))
    log("  decelerating (confirmed):")
    for r in warehouse.query("signal_movers",
                             "SELECT brand, week, units, prev_units, pct_change, matched_cells FROM t "
                             "WHERE NOT partial AND week = (SELECT MAX(week) FROM t WHERE NOT partial) "
                             "AND pct_change < 0 ORDER BY pct_change ASC LIMIT 5"):
        log("    ↓ %-24s %s  %g%% (%s→%s u · %s cells)"
            % (str(r["brand"])[:24], r["week"], r["pct_change"], int(r["prev_units"]), int(r["units"]), r["matched_cells"]))
    npart = warehouse.query("signal_movers", "SELECT COUNT(*) n FROM t WHERE partial")[0]["n"]
    log("  (%d partial-week mover rows flagged as early-read, excluded from the confirmed leaderboard)" % npart)
    log("[voids] top distribution opportunities (brand OOS → recoverable units/wk):")
    for r in warehouse.query("signal_voids",
                             "SELECT brand, oos_stores, stores_seen, est_recoverable_units_wk, pct_stores_out "
                             "FROM t ORDER BY est_recoverable_units_wk DESC LIMIT 8"):
        log("    ● %-24s out at %s/%s stores (%s%%) → ~%s recoverable units/wk"
            % (str(r["brand"])[:24], r["oos_stores"], r["stores_seen"], r["pct_stores_out"],
               int(r["est_recoverable_units_wk"] or 0)))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Velocity signals: movers + voids (MOAT V5).")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args(argv)
    build()
    if a.stats:
        stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
