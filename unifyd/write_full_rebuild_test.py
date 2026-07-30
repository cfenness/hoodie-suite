#!/usr/bin/env python3
"""write_full_rebuild_test.py — the manifest-aware full-replace warehouse.write_full_rebuild() must
land on a bucketed table (where a plain write_parquet REFUSES) and be a no-op passthrough elsewhere.

Fixing the 2026-07-29 OOM triage bucketizes `src_outlets` and `binnys_products` — both tables also have
a real, scheduled full-rebuild writer (normalize.py's re-shred, binnys_scraper.py's verified-complete-
crawl overwrite). Without this, the FIRST full rebuild after either table migrates would raise instead
of landing — trading one failure mode (OOM) for a guaranteed one (ValueError on every run). This proves
the fix holds, against a real DuckDB + pyarrow warehouse (not stubbed).

    python3 unifyd/write_full_rebuild_test.py
"""
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="wfr_test_")
warehouse._LOCAL_DIR = TMP

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


FIELDS = ["source", "store_id", "name", "lat"]


def rows(n, tag):
    return [{"source": "dd", "store_id": "s%d" % i, "name": "%s-%d" % (tag, i), "lat": float(i)}
            for i in range(n)]


def main():
    print("write_full_rebuild — manifest-aware full replace")

    # ── never bucketed: passthrough to plain write_parquet ─────────────────────────────────────────
    warehouse.write_full_rebuild("plain_table", rows(50, "v1"), fields=FIELDS)
    check("never-bucketed table stays flat (no manifest)", warehouse.read_manifest("plain_table") is None)
    check("plain rebuild landed all rows", warehouse.row_count("plain_table") == 50)
    warehouse.write_full_rebuild("plain_table", rows(30, "v2"), fields=FIELDS)
    check("a second plain rebuild REPLACES (not appends)", warehouse.row_count("plain_table") == 30)

    # ── bucketed: migrate, then prove write_parquet REFUSES (the bug this fixes) ───────────────────
    warehouse.write_parquet("bucketed_table", rows(200, "orig"), fields=FIELDS)
    warehouse.migrate_to_bucketed("bucketed_table", ["source", "store_id"], hex_len=1)
    check("migration landed a manifest", warehouse.read_manifest("bucketed_table") is not None)
    check("migration preserved row count", warehouse.row_count("bucketed_table") == 200)

    raised = None
    try:
        warehouse.write_parquet("bucketed_table", rows(10, "oops"), fields=FIELDS)
    except ValueError as e:
        raised = e
    check("write_parquet on a bucketed table still refuses (proves the landmine is real)",
          raised is not None, "should have raised")

    # ── the fix: write_full_rebuild lands on the SAME table without raising ────────────────────────
    res = warehouse.write_full_rebuild("bucketed_table", rows(75, "rebuilt"), fields=FIELDS)
    check("write_full_rebuild does not raise on a bucketed table", res is not None)
    check("row count reflects the NEW rebuild, not a merge with the old 200",
          warehouse.row_count("bucketed_table") == 75,
          "got %s" % warehouse.row_count("bucketed_table"))
    man = warehouse.read_manifest("bucketed_table")
    check("table is STILL bucketed after the rebuild (didn't silently fall back to flat)", man is not None)
    check("same key_cols preserved across the rebuild", man and man.get("key_cols") == ["source", "store_id"])
    check("same hex_len preserved across the rebuild", man and int(man.get("hex_len")) == 1)

    got = {r["store_id"]: r["name"] for r in warehouse.query("bucketed_table", "SELECT * FROM t")}
    check("query() returns exactly the rebuilt content (dual-read routes through the new manifest)",
          got == {"s%d" % i: "rebuilt-%d" % i for i in range(75)},
          "mismatch, %d rows read" % len(got))

    # ── the table must still ACCUMULATE correctly after a full rebuild (not just once) ─────────────
    warehouse.write_accumulate("bucketed_table", [{"source": "dd", "store_id": "s0", "name": "patched", "lat": 9.9}],
                               key=["source", "store_id"], fields=FIELDS)
    check("accumulate still works post-rebuild: table size unchanged (an update, not an insert)",
          warehouse.row_count("bucketed_table") == 75)
    patched = next(r for r in warehouse.query("bucketed_table", "SELECT * FROM t") if r["store_id"] == "s0")
    check("accumulate still works post-rebuild: the update actually landed", patched["name"] == "patched")

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
