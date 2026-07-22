#!/usr/bin/env python3
"""sipsource_ingest.py — land the SipSource feed and build the SERVING MARTS (NRT-PLAN.md Phase 4 / §1d).

The raw grain (~500M rows) is NEVER queried by the site. Ingest reads the hive-partitioned raw feed
(period=YYYYMM/market=XX/…) ONCE in DuckDB and rolls it up into a handful of small marts (a few
million rows each) that the console/API read. DuckDB streams the aggregation over Parquet with a
memory_limit (spills to disk), so the roll-up runs on the ephemeral worker, not the serving VM.

Marts (each with YoY via a 12-month self-join so % change is real, not invented):
  mart_sip_brand_market_month     brand × market × premise × month   — the workhorse drill grain
  mart_sip_supplier_cat_month     supplier × category × month        — portfolio view
  mart_sip_category_month         category × month (national)        — headline trends

Real-feed note: when the actual file arrives it lands to the SAME raw layout (a CSV/xlsx streamed to
period=…/market=… parquet); only the *reader* changes, never these marts or the API. This module is
the reader-agnostic half.

    python sipsource_ingest.py --raw sip_raw          # build all marts from the landed raw feed
    python sipsource_ingest.py --raw sip_raw --stats  # + print mart sizes / a scoped-query timing
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _raw_glob(name):
    import warehouse
    if warehouse.remote():
        return "s3://%s/%s/%s/**/*.parquet" % (warehouse._bucket(), warehouse._prefix(), name)
    return os.path.join(warehouse._LOCAL_DIR, name, "**", "*.parquet")


def _con(name):
    """A DuckDB connection with the raw feed mounted as a hive-partitioned view `raw`."""
    import warehouse
    con = warehouse.connect()
    con.execute("CREATE OR REPLACE VIEW raw AS SELECT * FROM read_parquet('%s', hive_partitioning=true, "
                "union_by_name=true)" % _raw_glob(name).replace("'", ""))
    return con


# Each mart: (key dims → the SMALL grain the site drills, carry → descriptive attrs pulled along).
# Mart size is bounded by the key cross-product, NOT by raw rows — the property that keeps a 500M-row
# feed servable from a few-million-row table. So keys stay coarse (no item_id, no distributor, no
# premise in the workhorse mart); brand→supplier and brand→category are carried, not keyed.
_MARTS = {
    "mart_sip_brand_market_month": {"keys": ["period", "market", "brand_id"],
                                    "carry": ["supplier_id", "category"]},   # ≤ brands×markets×months
    "mart_sip_supplier_cat_month": {"keys": ["period", "supplier_id", "category"], "carry": []},
    "mart_sip_category_month":     {"keys": ["period", "category"], "carry": []},
    "mart_sip_brand_premise_month": {"keys": ["period", "premise", "brand_id"], "carry": ["category"]},
}


def _build_one(con, table, spec, log=print):
    """Aggregate raw → one mart at its key grain (carrying descriptive attrs via ANY_VALUE), then attach
    YoY (same keys, period 12 months earlier). Written full-rebuild via warehouse (small table)."""
    keys, carry = spec["keys"], spec["carry"]
    gb = ", ".join(keys)
    carry_sel = "".join("ANY_VALUE(%s) AS %s, " % (c, c) for c in carry)
    keydims = [d for d in keys if d != "period"]
    joinkeys = " AND ".join("a.%s = b.%s" % (d, d) for d in keydims)
    agg = ("SELECT %s, %sSUM(cases_9l) AS cases_9l, SUM(cases_physical) AS cases_physical, "
           "SUM(revenue_usd) AS revenue_usd, COUNT(*) AS src_rows FROM raw GROUP BY %s" % (gb, carry_sel, gb))
    # period is 'YYYYMM' text; year-ago = same month, year-1 => int shift of 100.
    sql = (
        "WITH agg AS (%s) "
        "SELECT a.*, "
        "  CASE WHEN b.cases_9l > 0 THEN round((a.cases_9l - b.cases_9l) / b.cases_9l * 100, 1) END AS cases_yoy_pct, "
        "  CASE WHEN b.revenue_usd > 0 THEN round((a.revenue_usd - b.revenue_usd) / b.revenue_usd * 100, 1) END AS rev_yoy_pct "
        "FROM agg a LEFT JOIN agg b ON %s AND CAST(a.period AS INT) = CAST(b.period AS INT) + 100"
        % (agg, joinkeys if joinkeys else "TRUE")
    )
    rows = con.execute(sql).fetch_arrow_table()
    _write_arrow(table, rows)
    log("  %-32s %s rows" % (table, f"{rows.num_rows:,}"))
    return rows.num_rows


def _write_arrow(table_name, arrow_tbl):
    """Write a pyarrow Table straight to the warehouse parquet (no list-of-dicts round-trip)."""
    import pyarrow.parquet as pq
    import warehouse
    if warehouse.remote():
        warehouse._retry(lambda: pq.write_table(arrow_tbl, "%s/%s" % (warehouse._bucket(), warehouse._s3_key(table_name)),
                                                filesystem=warehouse._s3fs(), compression="zstd"), "mart %s" % table_name)
    else:
        p = warehouse._local_path(table_name)
        pq.write_table(arrow_tbl, p, compression="zstd")


def build_marts(raw="sip_raw", log=print):
    """Build every serving mart from the landed raw feed. Returns {mart: rows, seconds}."""
    t0 = time.time()
    con = _con(raw)
    out = {}
    for table, dims in _MARTS.items():
        out[table] = _build_one(con, table, dims, log=log)
    dt = round(time.time() - t0, 1)
    log("[sipsource] built %d marts in %.1fs from %s" % (len(_MARTS), dt, raw))
    return {"marts": out, "seconds": dt}


def stats(raw="sip_raw", log=print):
    """Prove it: raw row count + size, mart sizes, and a SCOPED site query timing (one brand, all
    months) — the query the API would actually run, served from the mart via partition-free small read."""
    import warehouse
    con = _con(raw)
    n_raw = con.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
    log("[stats] raw sip_raw: %s rows" % f"{n_raw:,}")
    for t in _MARTS:
        try:
            log("  %-32s %s rows" % (t, f"{warehouse.row_count(t):,}"))
        except Exception as e:
            log("  %-32s (missing: %s)" % (t, str(e)[:40]))
    # scoped query the console would run: one brand's 37-month trend from the mart
    bid = con.execute("SELECT brand_id FROM read_parquet('%s') LIMIT 1"
                      % warehouse.uri("mart_sip_brand_market_month").replace("'", "")).fetchone()[0]
    t0 = time.time()
    rows = warehouse.query("mart_sip_brand_market_month",
                           "SELECT period, SUM(cases_9l) c, SUM(revenue_usd) r FROM t WHERE brand_id = %d "
                           "GROUP BY period ORDER BY period" % bid)
    log("[stats] scoped mart query (brand %d, %d months) in %.0f ms" % (bid, len(rows), (time.time() - t0) * 1000))
    return {"raw_rows": n_raw, "sample_brand": int(bid), "scoped_months": len(rows)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build SipSource serving marts from the landed raw feed.")
    ap.add_argument("--raw", default="sip_raw")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args(argv)
    build_marts(a.raw)
    if a.stats:
        stats(a.raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
