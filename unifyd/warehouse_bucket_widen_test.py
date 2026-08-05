#!/usr/bin/env python3
"""warehouse_bucket_widen_test.py — a bucketed merge must be able to ADD a column.

THE BREAK THIS LOCKS DOWN. `_accumulate_bucketed` took its column list only from `man["fields"]`,
so `write_accumulate(fields=...)` was silently IGNORED for every bucketed table and no merge could
ever widen a schema. The v1 path honours `fields` (it forwards them to `write_parquet`), so the two
layouts disagreed about whether a schema could grow — and migrating a table to v2 quietly removed
the ability.

That is not theoretical. `ubereats_products` lost `section`/`subsection` (added to the write schema
after the table was first written); `ue_enrich` needs `section` to build its getMenuItemV1 request,
and without it every request returned `invalid_uuid`, so the job landed ZERO rows for weeks while
reporting success. Migrating that table to bucketed — done to fix a memory problem — also removed
the only path that could have restored the column.

Runs against a real local warehouse (no network). Needs duckdb + pyarrow; skips cleanly without them.

    python3 unifyd/warehouse_bucket_widen_test.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import duckdb  # noqa: F401
    import pyarrow  # noqa: F401
except ImportError:
    print("SKIP warehouse_bucket_widen_test (duckdb/pyarrow not installed here — runs on the Fly image)")
    sys.exit(0)

TMP = tempfile.mkdtemp(prefix="bktwiden_")
os.environ.pop("AWS_ENDPOINT_URL_S3", None)          # force LOCAL mode
os.environ["WAREHOUSE_LOCAL_DIR"] = TMP

import warehouse  # noqa: E402

warehouse._LOCAL_DIR = TMP
FAILED = []


def check(label, ok, detail=""):
    if not ok:
        FAILED.append(label)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label, ("\n     " + detail) if detail and not ok else ""))


def main():
    print("bucketed merge — schema widening")
    T = "widen_tbl"
    narrow = ["sku", "name"]
    wide = ["sku", "name", "section"]          # `section` is the column the real bug lost

    # a bucketed table that predates the new column
    warehouse.create_bucketed(T, ("sku",), narrow, hex_len=1)
    warehouse.write_accumulate(T, [{"sku": "A", "name": "apple"},
                                   {"sku": "B", "name": "beer"}],
                               key=("sku",), fields=narrow, coverage=False)
    man = warehouse.read_manifest(T)
    check("table starts narrow", man and man["fields"] == narrow, str(man and man["fields"]))

    # a later writer declares one more column
    warehouse.write_accumulate(T, [{"sku": "B", "name": "beer", "section": "S9"},
                                   {"sku": "C", "name": "cider", "section": "S7"}],
                               key=("sku",), fields=wide, coverage=False)

    man = warehouse.read_manifest(T)
    check("manifest records the wider schema", man["fields"] == wide, str(man["fields"]))
    check("widening is logged as a schema event",
          any(c.get("added_fields") == ["section"] for c in man.get("changelog", [])))

    rows = {r["sku"]: r for r in warehouse.query(T, "SELECT * FROM t")}
    check("no rows lost while widening", sorted(rows) == ["A", "B", "C"], str(sorted(rows)))
    check("new rows carry the new column", rows["C"].get("section") == "S7")
    check("re-written key takes the new value", rows["B"].get("section") == "S9")
    # The pre-existing row predates the column: NULL is correct, and it must not have been dropped.
    check("untouched pre-existing row survives with NULL in the new column",
          rows["A"].get("name") == "apple" and not rows["A"].get("section"))

    # a caller that omits a column must never DROP it — narrowing is destructive
    warehouse.write_accumulate(T, [{"sku": "D", "name": "dram"}],
                               key=("sku",), fields=narrow, coverage=False)
    man = warehouse.read_manifest(T)
    check("a narrower caller does NOT drop the column", man["fields"] == wide, str(man["fields"]))
    rows = {r["sku"]: r for r in warehouse.query(T, "SELECT * FROM t")}
    check("earlier widened values survive a narrower write", rows["C"].get("section") == "S7")

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%d failed" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
