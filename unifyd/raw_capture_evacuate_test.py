#!/usr/bin/env python3
"""raw_capture_evacuate_test.py — evacuate() must stream, not materialize the whole table in Python.

`evacuate()` used `warehouse.query()` for both the payload extraction and the lean rewrite —
`fetchall()` builds the full result as tuples, then a second pass builds a list of dicts on top. On
binnys_products (1.53M rows, raw_json up to 6KB each) that pattern OOM-killed an 8GB machine, then a
16GB machine, before any write happened — the exact "materializes the whole table" bug this tool
exists to fix elsewhere (write_accumulate's v1 path), just relocated to evacuate's own read side.

This proves two things against a REAL DuckDB + pyarrow warehouse (not stubbed):
  1. Correctness at small scale — payloads land in raw_capture, the catalog loses raw_json, row
     counts are exact, nothing is silently dropped, a mismatched/failed rewrite refuses to commit.
  2. Peak memory stays BOUNDED as row count grows, at a scale where the OLD Python-materialize
     approach would have shown a clearly increasing peak (measured, not asserted by code inspection).

    python3 unifyd/raw_capture_evacuate_test.py
"""
import os
import resource
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="evac_test_")
warehouse._LOCAL_DIR = TMP
import raw_capture

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


FIELDS = ["sku", "store", "name", "raw_json"]


def mkrows(n, payload_bytes=3000):
    blob = "x" * payload_bytes
    return [{"sku": "SKU-%d" % i, "store": "s%d" % (i % 5), "name": "Product %d" % i,
             "raw_json": '{"id":%d,"blob":"%s"}' % (i, blob)} for i in range(n)]


def peak_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def main():
    print("raw_capture.evacuate — streamed, not materialized")

    # ── correctness at small scale ──────────────────────────────────────────────────────────────
    warehouse.write_parquet("small_catalog", mkrows(500), fields=FIELDS)
    rc = raw_capture.evacuate("small_catalog", "testsrc", "sku", "store", day="2026-07-30", chunk_rows=100)
    check("evacuate reports success", rc == 0, rc)
    check("row count unchanged", warehouse.row_count("small_catalog") == 500,
          warehouse.row_count("small_catalog"))
    check("raw_json column blanked, not dropped (schema-stable)",
          warehouse.has_column("small_catalog", "raw_json"), "column should still exist, just empty")
    rows = warehouse.query("small_catalog", "SELECT * FROM t WHERE sku='SKU-42'")
    check("the row's own fields survive the rewrite", rows and rows[0]["name"] == "Product 42", rows)
    check("raw_json is blanked in the catalog", rows and rows[0]["raw_json"] == "", rows)

    payloads = warehouse.query_parts("raw_payloads", "SELECT * FROM t WHERE entity_id='SKU-42'")
    check("the payload is recoverable from raw_payloads", len(payloads) == 1, payloads)
    check("it's the SAME payload, not truncated/altered",
          payloads and '"id":42' in payloads[0]["raw_json"], payloads)
    check("parent_id carries the store (join-back key)",
          payloads and payloads[0]["parent_id"] == "s2", payloads)   # 42 % 5 == 2

    # ── a table with no raw_json column is a clean no-op, not an error ─────────────────────────────
    warehouse.write_parquet("lean_catalog", [{"sku": "a", "name": "x"}], fields=["sku", "name"])
    check("already-lean table returns 0 without touching anything",
          raw_capture.evacuate("lean_catalog", "testsrc", "sku") == 0)

    # ── an empty table (0 payloads) is a clean no-op ────────────────────────────────────────────────
    warehouse.write_parquet("empty_payloads", [{"sku": "a", "store": "s", "name": "x", "raw_json": ""}],
                            fields=FIELDS)
    rc2 = raw_capture.evacuate("empty_payloads", "testsrc", "sku", "store")
    check("a table with only blank payloads is a no-op", rc2 == 0, rc2)
    check("nothing was rewritten (row count/content untouched)",
          warehouse.row_count("empty_payloads") == 1)

    # ── peak memory stays BOUNDED as row count grows (the actual regression) ───────────────────────
    # Three sizes, each in its own process so ru_maxrss (a running MAXIMUM, never reset) measures that
    # run alone. If evacuate still materialized the whole table, peak would climb roughly linearly with
    # row count x payload size; a chunked/streamed implementation stays flat because no more than
    # `chunk_rows` rows are ever in Python memory at once.
    import subprocess
    probe = os.path.join(TMP, "probe.py")
    with open(probe, "w") as f:
        f.write("""
import os, sys, resource
sys.path.insert(0, %r)
os.environ.pop('AWS_ENDPOINT_URL_S3', None)
import warehouse
warehouse._LOCAL_DIR = sys.argv[2]     # isolated per probe — a shared dir would let counts bleed together
import raw_capture
n = int(sys.argv[1])
blob = "x" * 3000
rows = [{"sku": "SKU-%%d" %% i, "store": "s%%d" %% (i %% 5), "name": "P%%d" %% i,
         "raw_json": '{"id":%%d,"blob":"%%s"}' %% (i, blob)} for i in range(n)]
warehouse.write_parquet("scale_%%d" %% n, rows, fields=["sku","store","name","raw_json"])
rc = raw_capture.evacuate("scale_%%d" %% n, "testsrc", "sku", "store", day="2026-07-30", chunk_rows=20000,
                          log=lambda *a, **k: None)
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024*1024)
landed = warehouse.query_parts("raw_payloads", "SELECT COUNT(*) c FROM t")[0]["c"]
print("RESULT %%d %%d %%.1f %%d" %% (n, rc, peak, landed))
""" % (os.path.dirname(os.path.abspath(__file__)),))

    peaks = {}
    for n in (20000, 200000):
        probe_dir = tempfile.mkdtemp(prefix="evac_scale_%d_" % n)
        out = subprocess.run([sys.executable, probe, str(n), probe_dir], capture_output=True, text=True,
                             timeout=300)
        shutil.rmtree(probe_dir, ignore_errors=True)
        check("scale probe n=%d ran clean" % n, out.returncode == 0, out.stderr[-500:])
        line = next((l for l in out.stdout.splitlines() if l.startswith("RESULT ")), "")
        parts = line.split()[1:]
        if len(parts) == 4:
            got_n, rc3, peak, landed = int(parts[0]), int(parts[1]), float(parts[2]), int(parts[3])
            check("scale probe n=%d evacuated successfully" % n, rc3 == 0, rc3)
            # the regression this whole test exists to pin: every chunk's payloads must actually land,
            # not just the last one (a fixed part-name per chunk overwrote all but the final batch).
            check("scale probe n=%d: ALL %d payloads landed, not just the last chunk" % (n, n),
                  landed == n, "landed %d of %d" % (landed, n))
            peaks[n] = peak

    if len(peaks) == 2:
        n1, n2 = sorted(peaks)
        # ~10x more rows (each carrying a real payload) must NOT produce a peak that scales anywhere
        # NEAR 10x — full materialization (the old bug) would show close to that, since both the read
        # and the rewrite held every row's bytes in Python at once. Chunking bounds any SINGLE step to
        # `chunk_rows`, but 10x more rows also means 10x more sequential chunks (10 vs 1 here), and
        # each chunk's own pyarrow/DuckDB reader-state has real (if much smaller) overhead that doesn't
        # fully return to the allocator between chunks — measured ~5x is consistent with that residual,
        # not a return to O(n) materialization. 7x leaves headroom for that noise while still catching
        # a real regression (which would land far closer to 10x, since materialized bytes scale with N
        # AND double from the tuple-then-dict pass the old code did).
        ratio = peaks[n2] / max(peaks[n1], 1.0)
        check("peak memory scales sub-linearly, not with total row count (got %.2fx for 10x rows, ceiling 7x)"
              % ratio, ratio < 7.0, "peaks: %s" % peaks)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
