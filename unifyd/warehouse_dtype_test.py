"""warehouse_dtype_test.py — a field that's all-null in ONE batch must not corrupt a later union read.

Grounded in a real incident: ubereats_products_parts ended up with 5 distinct per-file schemas because
pa.Table.from_pylist() infers a column's type from THAT BATCH's data alone — a batch where `brand` (or
gtin/promo/size/abv/category) happened to be None for every item inferred as an int64/null column,
while another batch with real string values for the same field inferred VARCHAR. query_parts()'s
union_by_name across all partitions then had to reconcile VARCHAR against INTEGER for one column,
which corrupted the read (an invalid-UTF8 decode error, not a clean type error) instead of failing
loudly — and the registered consolidation build had been silently broken as a result. write_partition's
`dtypes` param pins each column's type explicitly, independent of what happens to be in one batch.

Local mode, throwaway dir, no network: python warehouse_dtype_test.py
"""
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import pyarrow as pa

TMP = tempfile.mkdtemp(prefix="wh_dtype_")
warehouse._LOCAL_DIR = TMP

passed = failed = 0


def ok(name, cond, detail=""):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name + (("  " + str(detail)) if not cond else ""))
    passed += bool(cond)
    failed += not cond


FIELDS = ["store_uuid", "brand", "price"]
DTYPES = {"brand": pa.string(), "price": pa.float64()}

try:
    # --- the bug, reproduced without dtypes: batch 1 has brand=None for everything ------------------
    warehouse.write_partition("t1", "batch1",
                              [{"store_uuid": "s1", "brand": None, "price": 9.99}], fields=FIELDS)
    # batch 2 has a real brand string
    warehouse.write_partition("t1", "batch2",
                              [{"store_uuid": "s2", "brand": "Acme", "price": 12.5}], fields=FIELDS)
    try:
        rows = warehouse.query_parts("t1", "SELECT * FROM t ORDER BY store_uuid")
        # some pyarrow versions happen to agree on a type here — only a real assertion if it disagrees
        reproduced = False
    except Exception as e:
        reproduced = True
        ok("without dtypes, an all-null batch can corrupt a later union read (reproduces the bug)",
           True, str(e)[:100])
    if not reproduced:
        print("note: this pyarrow version didn't reproduce the schema drift on its own — "
              "the fix is still asserted below regardless")

    # --- the fix: dtypes pins the column type regardless of this batch's content --------------------
    warehouse.write_partition("t2", "batch1",
                              [{"store_uuid": "s1", "brand": None, "price": 9.99}],
                              fields=FIELDS, dtypes=DTYPES)
    warehouse.write_partition("t2", "batch2",
                              [{"store_uuid": "s2", "brand": "Acme", "price": 12.5}],
                              fields=FIELDS, dtypes=DTYPES)
    rows = warehouse.query_parts("t2", "SELECT * FROM t ORDER BY store_uuid")
    ok("both partitions read back together without raising", len(rows) == 2, rows)
    ok("the all-null batch's brand reads as None, not miscoerced", rows[0]["brand"] is None, rows)
    ok("the real-string batch's brand reads back correctly", rows[1]["brand"] == "Acme", rows)
    ok("numeric field survives across both batches", [r["price"] for r in rows] == [9.99, 12.5], rows)

    # --- an all-null batch alone (no other partition yet) still gets the pinned type -----------------
    warehouse.write_partition("t3", "onlybatch",
                              [{"store_uuid": "s1", "brand": None, "price": 1.0}],
                              fields=FIELDS, dtypes=DTYPES)
    rows = warehouse.query_parts("t3", "SELECT * FROM t")
    ok("a lone all-null batch still lands and reads back fine", len(rows) == 1 and rows[0]["brand"] is None, rows)

    # --- empty records list (no rows at all) with dtypes doesn't crash -------------------------------
    warehouse.write_partition("t4", "empty", [], fields=FIELDS, dtypes=DTYPES)
    rows = warehouse.query_parts("t4", "SELECT * FROM t")
    ok("writing zero records with dtypes set doesn't raise, and reads back empty", rows == [], rows)

    # --- backward compat: no dtypes at all still works (existing callers unaffected) -----------------
    warehouse.write_partition("t5", "batch1", [{"store_uuid": "s1", "brand": "X", "price": 1.0}],
                              fields=FIELDS)
    rows = warehouse.query_parts("t5", "SELECT * FROM t")
    ok("omitting dtypes entirely still works (no regression for existing callers)",
       len(rows) == 1 and rows[0]["brand"] == "X", rows)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
