"""Golden compatibility tests for warehouse v2 (bucketed layout): python warehouse_compat_test.py
The contract under test (NRT-PLAN.md §8d/§8e): identical calls against the v1 single-file and v2
bucketed layouts must return identical results, and every v1 behavior (empty-clobber guard,
accumulate semantics, row_count, list_datasets) survives unchanged. Local mode, throwaway dir,
no network."""
import os
import shutil
import sys
import tempfile

# Force LOCAL mode no matter what creds the shell has — these tests must never touch Tigris.
for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="wh_compat_")
warehouse._LOCAL_DIR = TMP

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


def rows_set(rows):
    """Order-insensitive canonical form for comparing whole tables."""
    return sorted((r["sku"], r["store"], r["price"], r["stock"]) for r in rows)


FIELDS = ["sku", "store", "price", "stock"]


def mk(n, store="S1", price="9.99", stock="in"):
    return [dict(sku="SKU%05d" % i, store=store, price=price, stock=stock) for i in range(n)]


KEY = lambda r: "%s|%s" % (r["sku"], r["store"])

# ── 1) v1 behavior unchanged ────────────────────────────────────────────────────────────────────
base = mk(500) + mk(500, store="S2")
warehouse.write_parquet("cat_v1", base, fields=FIELDS)
ok("v1 write/query roundtrip", rows_set(warehouse.query("cat_v1")) == rows_set(base))
ok("v1 row_count", warehouse.row_count("cat_v1") == 1000)

try:
    warehouse.write_parquet("cat_v1", [], fields=FIELDS)
    ok("v1 empty-clobber guard", False)
except ValueError:
    ok("v1 empty-clobber guard", True)

# v1 accumulate = the reference semantics v2 must reproduce
upd = mk(50, price="7.77") + mk(25, store="S3")
warehouse.write_parquet("cat_ref", base, fields=FIELDS)
warehouse.write_accumulate("cat_ref", upd, key=KEY, fields=FIELDS)
ref = rows_set(warehouse.query("cat_ref"))
ok("v1 accumulate count", warehouse.row_count("cat_ref") == 1025)

# ── 2) migrate: same data → same answers ────────────────────────────────────────────────────────
warehouse.write_parquet("cat_v2", base, fields=FIELDS)
res = warehouse.migrate_to_bucketed("cat_v2", key_cols=["sku", "store"], hex_len=1)
ok("migrate reports rows", res["rows"] == 1000)
ok("v2 row_count == v1", warehouse.row_count("cat_v2") == 1000)
ok("v2 query == v1 rows", rows_set(warehouse.query("cat_v2")) == rows_set(base))

# ── 3) accumulate on v2 == v1 reference ─────────────────────────────────────────────────────────
warehouse.write_accumulate("cat_v2", upd, key=KEY, fields=FIELDS)
ok("v2 accumulate == v1 result", rows_set(warehouse.query("cat_v2")) == ref)
ok("v2 accumulate count", warehouse.row_count("cat_v2") == 1025)
dup = warehouse.query("cat_v2", "SELECT COUNT(*) c FROM "
                                "(SELECT sku, store FROM t GROUP BY 1, 2 HAVING COUNT(*) > 1)")
ok("v2 no duplicate keys", dup[0]["c"] == 0)

# ── 4) python/SQL bucket agreement: every row sits in the bucket python computes ────────────────
man = warehouse.read_manifest("cat_v2")
agree = True
for b, part in man["parts"].items():
    for f in part["files"]:
        got = warehouse.connect().execute(
            "SELECT sku, store FROM read_parquet('%s')" % warehouse._part_sql_path(f)).fetchall()
        for sku, store in got:
            if warehouse._bucket_hex("%s|%s" % (sku, store), man["hex_len"]) != b:
                agree = False
ok("python/SQL bucket agreement", agree)

# ── 5) single-file overwrite refused on a migrated table ────────────────────────────────────────
try:
    warehouse.write_parquet("cat_v2", mk(3), fields=FIELDS)
    ok("v2 overwrite refused", False)
except ValueError:
    ok("v2 overwrite refused", True)

# ── 6) list_datasets surfaces the LIVE layout (not the rollback copy) ───────────────────────────
ds = {d["name"]: d for d in warehouse.list_datasets()}
ok("list_datasets v2 entry", ds.get("cat_v2", {}).get("rows") == 1025)
ok("list_datasets v1 entry intact", ds.get("cat_v1", {}).get("rows") == 1000)

# ── 7) changelog: the canon seam ────────────────────────────────────────────────────────────────
log = warehouse.manifest_changelog("cat_v2")
ok("changelog ops recorded", [e["op"] for e in log] == ["migrate", "accumulate"])
ok("changelog buckets + version", len(log[1]["buckets"]) > 0 and log[1]["version"] == 2)
ok("changelog since-filter", warehouse.manifest_changelog("cat_v2", since_version=1) == log[1:])
ok("changelog empty for v1 tables", warehouse.manifest_changelog("cat_v1") == [])

# ── 8) rollback: manifest dropped → reads fall back to the kept single file ─────────────────────
warehouse.rollback_to_single("cat_v2")
ok("rollback row_count = original", warehouse.row_count("cat_v2") == 1000)
ok("rollback query = original", rows_set(warehouse.query("cat_v2")) == rows_set(base))

# ── 9) born-bucketed (create_bucketed): accumulate from empty, updates apply ────────────────────
warehouse.create_bucketed("cat_new", key_cols=["sku", "store"], fields=FIELDS, hex_len=1)
warehouse.write_accumulate("cat_new", mk(40), key=KEY, fields=FIELDS)
warehouse.write_accumulate("cat_new", mk(10, price="1.11") + mk(5, store="S9"), key=KEY, fields=FIELDS)
ok("born-bucketed count", warehouse.row_count("cat_new") == 45)
low = warehouse.query("cat_new", "SELECT price FROM t WHERE store = 'S1' "
                                 "AND CAST(substr(sku, 4) AS INT) < 10")
ok("born-bucketed update applied", len(low) == 10 and all(r["price"] == "1.11" for r in low))

# ── 10) _retry: transient S3 errors retried, permanent errors raised immediately ────────────────
os.environ["WAREHOUSE_WRITE_RETRIES"] = "4"
calls = {"n": 0}


def flaky(n_fail, exc):
    calls["n"] = 0

    def fn():
        calls["n"] += 1
        if calls["n"] <= n_fail:
            raise exc
        return "ok"
    return fn

ok("retry: succeeds after transient blips",
   warehouse._retry(flaky(2, OSError("AWS Error NETWORK_CONNECTION ... curlCode: 28"))) == "ok" and calls["n"] == 3)

# Live occurrence (Cockpit preview-snapshot, 2026-07-30): the FIRST-EVER write to a brand-new small
# key failed outright with zero retries — "NO_SUCH_UPLOAD during CompleteMultipartUpload" matched
# none of the existing markers, same failure shape as the read-side gap this file already documents.
ok("retry: NO_SUCH_UPLOAD (first-write multipart race) is now retried",
   warehouse._retry(flaky(2, OSError(
       "When completing multiple part upload for key 'x/index.json' in bucket 'y': "
       "AWS Error NO_SUCH_UPLOAD during CompleteMultipartUpload"))) == "ok" and calls["n"] == 3)

try:
    warehouse._retry(flaky(9, OSError("curlCode: 28, Timeout was reached")))
    ok("retry: gives up after the cap", False)
except OSError:
    ok("retry: gives up after the cap", calls["n"] == 4)

try:
    warehouse._retry(flaky(1, ValueError("AccessDenied: bad key")))
    ok("retry: permanent error raised immediately", False)
except ValueError:
    ok("retry: permanent error raised immediately", calls["n"] == 1)

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
