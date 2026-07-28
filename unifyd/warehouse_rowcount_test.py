"""Regression guard: row_count / row_count_strict must see the DATE-PARTITIONED layout.

The bug this locks down: both counters resolved a table to `<name>.parquet`, so a table written by
write_partition (one file per date×source) 404'd and was classified as genuine absence — a confident,
wrong 0. retail_observations reported 0 while holding 15,127,465 rows, which meant run_sources'
landing verification recorded delta=0 on every run for every partitioned source and forced their
status to `empty` no matter what they landed.

Runs entirely in LOCAL mode (no object storage, no creds) so it is cheap to run anywhere.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Force local mode BEFORE importing warehouse resolves its config.
for k in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(k, None)

import warehouse  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wh_rowcount_")
warehouse._LOCAL_DIR = TMP


def _rows(n, tag):
    return [{"date": "2026-07-2%d" % (i % 10), "source": tag, "sku": "S%04d" % i, "price": 1.0 * i}
            for i in range(n)]


try:
    assert not warehouse.remote(), "test must run in local mode"

    # ── 1. a genuinely absent table is still 0, not a fabricated number ─────────────────────────
    assert warehouse.row_count("nope_not_here") == 0
    assert warehouse.row_count_strict("nope_not_here") == 0
    print("PASS: absent table still reads 0")

    # ── 2. single-file layout unchanged ────────────────────────────────────────────────────────
    warehouse.write_parquet("plain_tbl", _rows(37, "x"))
    assert warehouse.row_count("plain_tbl") == 37, warehouse.row_count("plain_tbl")
    assert warehouse.row_count_strict("plain_tbl") == 37
    assert warehouse._partition_files("plain_tbl") == [], "single-file table must not look partitioned"
    print("PASS: single-file layout unchanged (37)")

    # ── 3. THE REGRESSION: date-partitioned table must sum its parts ────────────────────────────
    warehouse.write_partition("obs_tbl", "2026-07-20_srcA", _rows(100, "srcA"))
    warehouse.write_partition("obs_tbl", "2026-07-21_srcA", _rows(250, "srcA"))
    warehouse.write_partition("obs_tbl", "2026-07-21_srcB", _rows(75, "srcB"))
    parts = warehouse._partition_files("obs_tbl")
    assert len(parts) == 3, "expected 3 parts, saw %d" % len(parts)
    got = warehouse.row_count_strict("obs_tbl")
    assert got == 425, "PARTITIONED COUNT WRONG: expected 425, got %r (0 = the original bug)" % got
    assert warehouse.row_count("obs_tbl") == 425
    print("PASS: partitioned table sums its parts (425 across 3 parts)")

    # ── 4. a new part moves the count — this is what landing verification depends on ────────────
    warehouse.write_partition("obs_tbl", "2026-07-22_srcA", _rows(60, "srcA"))
    after = warehouse.row_count_strict("obs_tbl")
    assert after == 485, "expected 485 after a new part, got %r" % after
    assert after - got == 60, "delta must reflect the landed part, got %r" % (after - got)
    print("PASS: a landed partition moves the count (+60) — delta is measurable again")

    print()
    print("ALL ASSERTIONS PASSED")
finally:
    shutil.rmtree(TMP, ignore_errors=True)
