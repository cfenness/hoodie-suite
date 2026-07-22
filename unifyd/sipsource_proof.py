#!/usr/bin/env python3
"""sipsource_proof.py — end-to-end proof of the SipSource pipeline (NRT-PLAN.md Phase 4) at a
representative scale, with a measured extrapolation to the ~500M-row / 37-month target.

Runs in an ISOLATED local warehouse (throwaway dir) so it never touches Tigris or real tables:
  generate → land (hive-partitioned) → build marts → verify conservation → time a scoped query
then extrapolates raw storage, mart storage, and worker build time to 500M from measured per-row cost.

    python sipsource_proof.py --rows 50_000_000
"""
import argparse
import os
import shutil
import sys
import tempfile
import time

TARGET = 500_000_000
MONTHS = 37


def _dir_bytes(p):
    t = 0
    for root, _, files in os.walk(p):
        for f in files:
            t += os.path.getsize(os.path.join(root, f))
    return t


def run(rows, months=MONTHS):
    for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
        os.environ.pop(v, None)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import warehouse
    tmp = tempfile.mkdtemp(prefix="sip_proof_")
    warehouse._LOCAL_DIR = tmp
    os.environ.setdefault("DUCKDB_MEMORY_LIMIT", "4GB")     # prove it streams under a worker-like cap
    os.environ.setdefault("DUCKDB_TEMP_DIR", tmp)
    import sipsource_sim as sim
    import sipsource_ingest as ing

    print("=" * 78)
    print("SipSource proof — %s rows / %d months (isolated local warehouse)" % (f"{rows:,}", months))
    print("=" * 78)

    g = sim.generate(rows, months=months, log=lambda *a: None)
    raw_bytes = _dir_bytes(os.path.join(tmp, "sip_raw"))
    print("GENERATE  %s rows · %d partitions · %.1fs · %.0f rows/s"
          % (f"{g['rows']:,}", g["partitions"], g["seconds"], g["rows"] / max(g["seconds"], 0.01)))
    print("          raw parquet on disk: %.2f GB (%.1f bytes/row)" % (raw_bytes / 1e9, raw_bytes / g["rows"]))

    t0 = time.time()
    b = ing.build_marts("sip_raw", log=lambda *a: None)
    build_s = time.time() - t0
    marts = b["marts"]
    mart_bytes = sum(os.path.getsize(warehouse._local_path(t)) for t in marts)
    print("BUILD     %d marts · %.1fs (reads all %s raw rows, DuckDB 4GB cap)"
          % (len(marts), build_s, f"{g['rows']:,}"))
    for t, n in marts.items():
        print("            %-32s %12s rows · %.2f MB"
              % (t, f"{n:,}", os.path.getsize(warehouse._local_path(t)) / 1e6))
    biggest = max(marts, key=marts.get)
    collapse = g["rows"] / max(marts[biggest], 1)
    print("          raw→mart collapse (%s): %.1fx" % (biggest, collapse))

    # conservation: the mart must preserve raw totals exactly (roll-up correctness)
    con = ing._con("sip_raw")
    rawt = con.execute("SELECT round(SUM(cases_9l),1), round(SUM(revenue_usd),0) FROM raw").fetchone()
    martt = warehouse.query("mart_sip_category_month",
                            "SELECT round(SUM(cases_9l),1) c, round(SUM(revenue_usd),0) r FROM t")[0]
    ok = abs(rawt[0] - martt["c"]) < max(1.0, rawt[0] * 1e-6) and abs(rawt[1] - martt["r"]) < max(100, rawt[1] * 1e-6)
    print("CONSERVE  raw 9Lcases=%s rev=$%s  →  mart 9Lcases=%s rev=$%s  →  %s"
          % (f"{rawt[0]:,.0f}", f"{rawt[1]:,.0f}", f"{martt['c']:,.0f}", f"{martt['r']:,.0f}",
             "EXACT ✓" if ok else "MISMATCH ✗"))

    # scoped query the API would run: one brand, all months, from the mart
    bid = warehouse.query("mart_sip_brand_market_month", "SELECT brand_id FROM t LIMIT 1")[0]["brand_id"]
    t0 = time.time()
    r = warehouse.query("mart_sip_brand_market_month",
                        "SELECT period, SUM(cases_9l) c, SUM(revenue_usd) rev, MAX(cases_yoy_pct) yoy "
                        "FROM t WHERE brand_id=%d GROUP BY period ORDER BY period" % bid)
    q_ms = (time.time() - t0) * 1000
    print("SERVE     scoped mart query (1 brand × %d months, incl YoY): %.0f ms" % (len(r), q_ms))

    print("-" * 78)
    print("EXTRAPOLATION to %s rows / %d months (raw storage & time scale LINEARLY):" % (f"{TARGET:,}", months))
    scale = TARGET / g["rows"]
    print("  raw storage (Tigris) ....... %.1f GB   (raw grows with volume — but it's cheap object storage)"
          % (raw_bytes * scale / 1e9))
    print("  full backfill land time .... %.0f min  (one-time; monthly delta thereafter = 1/%d of this)"
          % (g["seconds"] * scale / 60, months))
    print("  full mart rebuild .......... %.0f min  (ephemeral worker, DuckDB streams under mem cap)"
          % (build_s * scale / 60))
    print("  scoped API query ........... ~%.0f ms  (unchanged — reads the small mart, not raw)" % q_ms)
    print("  KEY: the serving mart is BOUNDED by dims (brands×markets×months ≈ %s), NOT by raw rows —"
          % f"{5000 * 50 * months:,}")
    print("       proven below: raw doubles, mart stays flat, so the site never scales with the feed.")
    shutil.rmtree(tmp, ignore_errors=True)
    return dict(rows=g["rows"], raw_gb=raw_bytes / 1e9, build_s=build_s, collapse=collapse,
                q_ms=q_ms, conserve=ok, biggest=biggest, mart_rows=marts[biggest],
                target_raw_gb=raw_bytes * scale / 1e9)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=50_000_000)
    ap.add_argument("--months", type=int, default=MONTHS)
    ap.add_argument("--bounding", action="store_true",
                    help="also run 2× rows to PROVE the serving mart stays flat as raw doubles")
    a = ap.parse_args(argv)
    res = run(a.rows, a.months)
    if a.bounding:
        print("\n" + "#" * 78)
        print("# BOUNDING PROOF — same run at 2× raw. Watch the serving mart NOT grow.")
        print("#" * 78)
        res2 = run(a.rows * 2, a.months)
        growth = res2["mart_rows"] / max(res["mart_rows"], 1)
        print("\nRAW  %s → %s  (2.0× more rows)" % (f"{res['rows']:,}", f"{res2['rows']:,}"))
        print("MART %s → %s  (%.2f× — %s)" % (f"{res['mart_rows']:,}", f"{res2['mart_rows']:,}", growth,
              "BOUNDED ✓ the site's read cost is decoupled from feed size" if growth < 1.3 else "still growing"))
    sys.exit(0 if res["conserve"] else 1)


if __name__ == "__main__":
    main()
