#!/usr/bin/env python3
"""obs_quality.py — the OBSERVATION-QUALITY layer (MOAT-PLAN.md Workstream V1).

Before we trust the instrument, we measure it. Velocity (V2+) reads inventory deltas out of
`retail_observations`; the confidence on every velocity number traces back to HOW WELL each cell is
observed. This build produces that error model — two grains, both pure DuckDB aggregation (no Python
row loops — binny's alone is 7.3M obs), landed to the warehouse:

  obs_quality_source   one row per source — the INSTRUMENT card: cadence, coverage, and crucially
                       whether `qty` is a real unit count or a STATUS-BUCKET in disguise (a source
                       that only ever reports qty ∈ {0,1} or a dozen round numbers is not counting).
  obs_quality_cell     one row per (source, store_id, product_id) — the velocity substrate: how many
                       times seen, over how long, at what cadence, with a jitter fingerprint (count
                       wobble WITHOUT a price/promo change = shelf-count noise, not a sale).

Nothing here estimates a sale yet (that's V2). This is: "how good is each cell's data, and which
sources are lying about having counts." Every downstream confidence cites `qual_tier` / `noise` here.

    python obs_quality.py            # rebuild both tables from retail_observations
    python obs_quality.py --stats    # + print the instrument cards
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# qty-semantics thresholds (see classify): how we decide a `qty` column is a real count vs a disguised
# status bucket. Tunable via env for the calibration pass; defaults from the observed instrument
# profile (real counters show hundreds+ of distinct integer levels; status-buckets show a handful).
_MIN_DISTINCT_QTY = int(os.environ.get("OBSQ_MIN_DISTINCT_QTY", "8"))   # fewer distinct values ⇒ bucket
_COUNT_COVERAGE = float(os.environ.get("OBSQ_COUNT_COVERAGE", "0.5"))   # qty present on ≥50% of rows ⇒ has counts


def _src():
    """The retail_observations time-series as view `t` (all dated/source partitions)."""
    import warehouse
    return warehouse


def build(log=print):
    """Rebuild obs_quality_source + obs_quality_cell from retail_observations. Returns row counts."""
    import warehouse
    con = warehouse.connect()
    # mount the whole time-series once; both aggregations read this view
    glob = (("s3://%s/%s/retail_observations/*.parquet" % (warehouse._bucket(), warehouse._prefix()))
            if warehouse.remote() else os.path.join(warehouse._LOCAL_DIR, "retail_observations", "*.parquet"))
    con.execute("CREATE OR REPLACE VIEW obs AS SELECT * FROM read_parquet('%s', union_by_name=true)"
                % glob.replace("'", ""))

    # ── per-(source, store, sku) CELL quality ────────────────────────────────────────────────────
    # product_id is the source's own key (matches observe.record); store_id the store. A cell is one
    # product at one store across time. price_changes / qty_moves let us separate real movement from
    # noise. qty_jitter = moves where NEITHER price NOR promo changed AND the move is tiny (±1..2):
    # that's shelf-recount wobble, not a sale — V2 damps it.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE cell AS
        WITH seq AS (
            SELECT source, store_id, product_id, upc, brand, date,
                   TRY_CAST(qty AS DOUBLE) AS qty, TRY_CAST(price AS DOUBLE) AS price,
                   COALESCE(TRY_CAST(on_promo AS BOOLEAN), false) AS promo,
                   CASE WHEN lower(CAST(in_stock AS VARCHAR)) IN ('true','1','t','yes') THEN 1
                        WHEN lower(CAST(in_stock AS VARCHAR)) IN ('false','0','f','no') THEN 0 END AS instk
            FROM obs WHERE store_id IS NOT NULL AND product_id IS NOT NULL
        ),
        lagged AS (
            SELECT *,
                   lag(qty)   OVER w AS pq,
                   lag(price) OVER w AS pp,
                   lag(promo) OVER w AS ppr
            FROM seq
            WINDOW w AS (PARTITION BY source, store_id, product_id ORDER BY date)
        )
        SELECT source, store_id, product_id,
               any_value(upc) AS upc, any_value(brand) AS brand,
               COUNT(*)                      AS n_obs,
               COUNT(DISTINCT date)          AS n_days,
               MIN(date)                     AS first_date,
               MAX(date)                     AS last_date,
               COUNT(qty)                    AS n_qty,
               COUNT(DISTINCT qty)           AS distinct_qty,
               SUM(CASE WHEN pq IS NOT NULL AND qty <> pq THEN 1 ELSE 0 END)              AS qty_moves,
               SUM(CASE WHEN pp IS NOT NULL AND price <> pp THEN 1 ELSE 0 END)            AS price_moves,
               -- jitter: qty moved by ±1..2 with NO price change and NO promo change = recount noise
               SUM(CASE WHEN pq IS NOT NULL AND qty <> pq AND abs(qty - pq) <= 2
                        AND (pp IS NULL OR price = pp) AND (ppr IS NULL OR promo = ppr)
                        THEN 1 ELSE 0 END)                                                AS jitter_moves
        FROM lagged
        GROUP BY source, store_id, product_id
    """)
    # cadence per cell: mean gap in days across its observed dates (n_days>1), else null
    con.execute("""
        ALTER TABLE cell ADD COLUMN cadence_days DOUBLE;
        UPDATE cell SET cadence_days = CASE WHEN n_days > 1
            THEN (date_diff('day', TRY_CAST(first_date AS DATE), TRY_CAST(last_date AS DATE)) * 1.0)
                 / (n_days - 1) END;
    """)
    cell_rows = con.execute("SELECT COUNT(*) FROM cell").fetchone()[0]
    _write(con, "obs_quality_cell", "SELECT * FROM cell")

    # ── per-source INSTRUMENT card ───────────────────────────────────────────────────────────────
    # qty semantics: has_counts = qty present on enough rows AND enough distinct values to be a real
    # count (not a {0,1} in/out flag or a handful of round buckets). qual_tier ranks the instrument.
    # global qty cardinality per source, straight from obs (a real counter shows many distinct integer
    # levels across the book; an in/out flag or round-number bucket shows a handful — the disguise test).
    con.execute("""
        CREATE OR REPLACE TEMP TABLE srcqty AS
        SELECT source, COUNT(DISTINCT TRY_CAST(qty AS DOUBLE)) AS distinct_qty_global
        FROM obs WHERE qty IS NOT NULL GROUP BY source
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE srccard AS
        WITH s AS (
            SELECT source,
                   SUM(n_obs)                          AS obs,
                   COUNT(*)                            AS cells,
                   SUM(n_qty)                          AS qty_obs,
                   SUM(qty_moves)                      AS qty_moves,
                   SUM(jitter_moves)                   AS jitter_moves,
                   MIN(first_date)                     AS first_date,
                   MAX(last_date)                      AS last_date,
                   COUNT(DISTINCT store_id)            AS stores,
                   median(cadence_days)                AS median_cadence_days,
                   SUM(CASE WHEN n_obs > 1 THEN 1 ELSE 0 END) AS diffable_cells
            FROM cell GROUP BY source
        )
        SELECT s.source, s.obs, s.cells, s.stores, s.first_date, s.last_date,
               round(s.qty_obs * 1.0 / NULLIF(s.obs, 0), 3)        AS qty_coverage,
               COALESCE(q.distinct_qty_global, 0)                  AS distinct_qty_global,
               round(s.diffable_cells * 1.0 / NULLIF(s.cells, 0), 3) AS diffable_frac,
               round(s.jitter_moves * 1.0 / NULLIF(s.qty_moves, 0), 3) AS jitter_frac,
               round(s.median_cadence_days, 2)                     AS median_cadence_days,
               (s.qty_obs * 1.0 / NULLIF(s.obs,0) >= %f
                AND COALESCE(q.distinct_qty_global,0) >= %d)        AS has_counts
        FROM s LEFT JOIN srcqty q USING (source)
    """ % (_COUNT_COVERAGE, _MIN_DISTINCT_QTY))
    # qual_tier: COUNT (trustworthy unit counts) > STATE (in/out only) > THIN (too few repeat obs to diff)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE srctier AS
        SELECT *,
            CASE WHEN diffable_frac < 0.05 THEN 'thin'
                 WHEN has_counts THEN 'count'
                 ELSE 'state' END AS qual_tier
        FROM srccard
    """)
    src_rows = con.execute("SELECT COUNT(*) FROM srctier").fetchone()[0]
    _write(con, "obs_quality_source", "SELECT * FROM srctier")

    log("[obs_quality] %d source cards, %d cells -> obs_quality_source / obs_quality_cell"
        % (src_rows, cell_rows))
    return {"sources": src_rows, "cells": cell_rows}


def _write(con, table, select_sql):
    """Materialize a query to a warehouse parquet (arrow → write, no list-of-dicts)."""
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
    cards = warehouse.query("obs_quality_source",
                            "SELECT * FROM t ORDER BY obs DESC")
    log("%-14s %-6s %9s %8s %6s %6s %7s %8s %8s" %
        ("source", "tier", "obs", "cells", "qtyC", "diffF", "jitter", "cadence", "counts?"))
    for r in cards:
        log("%-14s %-6s %9s %8s %6s %6s %7s %8s %8s" % (
            r["source"], r["qual_tier"], f"{r['obs']:,}", f"{r['cells']:,}",
            r["qty_coverage"], r["diffable_frac"], r["jitter_frac"],
            r["median_cadence_days"], "yes" if r["has_counts"] else "—"))
    return cards


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the observation-quality error model (MOAT V1).")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args(argv)
    build()
    if a.stats:
        stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
