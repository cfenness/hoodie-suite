#!/usr/bin/env python3
"""warehouse_bucket_flush_test.py — a bucketed migration must land ONE file per bucket.

THE BREAK THIS LOCKS DOWN. `migrate_to_bucketed` sets DuckDB's
`partitioned_write_flush_threshold`, which is GLOBAL, not per-partition: every flush emits one file
per OPEN partition. At the old value of 4096 a 597k-row table flushed ~146 times and produced tens of
thousands of ~40-row files instead of 256.

Why it needs a mechanical guard rather than care: the fragmented migration still VERIFIES. Row count
and distinct-key count both match, the manifest is written, nothing errors — the table is simply now
tens of thousands of objects, and every later read pays for it. Nothing in the pipeline can see that,
which is the same shape as every other quiet degrade in this repo.

Measured on the real shape (600k rows -> 256 md5 buckets):
    open=8   flush=4096      -> 36,609 files
    open=64  flush=4096      -> 28,433 files   (widening the CAP makes it WORSE)
    open=64  flush=1_000_000 ->    256 files   (one per bucket)

Needs duckdb; skips cleanly without it, like the other warehouse tests.

    python3 unifyd/warehouse_bucket_flush_test.py
"""
import glob
import os
import shutil
import sys
import tempfile

try:
    import duckdb
except ImportError:
    print("SKIP warehouse_bucket_flush_test (duckdb not installed here — runs on the Fly image)")
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILED = []


def check(label, ok, detail=""):
    if not ok:
        FAILED.append(label)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label, ("  -- " + detail) if detail and not ok else ""))


def files_for(flush, open_files=64, rows=200_000, hex_len=2):
    d = tempfile.mkdtemp(prefix="bktflush_")
    out = os.path.join(d, "t")
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET threads=1")
    for p, v in (("partitioned_write_max_open_files", open_files),
                 ("partitioned_write_flush_threshold", flush)):
        try:
            con.execute("SET %s=%d" % (p, v))
        except Exception:
            pass
    con.execute(
        "COPY (SELECT i, substr(md5(i::VARCHAR),1,%d) AS __b FROM range(%d) t(i)) "
        "TO '%s' (FORMAT PARQUET, PARTITION_BY (__b))" % (hex_len, rows, out))
    n = len(glob.glob(out + "/**/*.parquet", recursive=True))
    b = len(glob.glob(out + "/__b=*"))
    shutil.rmtree(d, ignore_errors=True)
    return n, b


def main():
    print("bucketed-migration flush threshold")

    # 1. the value migrate_to_bucketed actually uses must be the high one
    src = open(os.path.join(HERE, "warehouse.py"), encoding="utf-8").read()
    check("migrate_to_bucketed no longer pins the shredding 4096 threshold",
          "partitioned_write_flush_threshold=4096" not in src)
    check("it sets a threshold large enough to buffer a whole bucket",
          "_flush = 1_000_000" in src)

    # 2. prove the behaviour, not just the constant
    n_hi, b_hi = files_for(1_000_000)
    check("high threshold -> exactly one file per bucket",
          n_hi == b_hi and b_hi > 1, "got %d files across %d buckets" % (n_hi, b_hi))

    n_lo, _ = files_for(4096)
    check("the old threshold really was the cause (it fragments)",
          n_lo > n_hi * 5, "old=%d new=%d — expected the old to be far worse" % (n_lo, n_hi))

    print("\n%d failed" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
