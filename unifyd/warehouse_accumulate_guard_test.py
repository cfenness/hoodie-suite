"""write_accumulate's read-failure guard: python warehouse_accumulate_guard_test.py

Needs pyarrow + duckdb (skips cleanly without them). Uses a local warehouse dir — no network, no creds.

THE BUG THIS PINS. `write_accumulate` merges by reading the whole table, dropping the keys being
replaced, and rewriting: `merged = existing + records`. The read used to be wrapped in a bare
`except Exception: existing = []` — so a TRANSIENT read failure did not degrade the merge, it
DESTROYED the table: the rewrite landed this batch alone.

Measured live 2026-08-03 on the salsify BBG crawl: two of 139 chunks (p0005, p0068) lost their rows
exactly this way — 800 products gone — while their append-only property partitions landed fine. The
run reported `ok`, because row-count verify-landing compares before/after totals and later chunks
re-grew the table past its previous size. A silent hole in a catalog is worse than a failed run.

The rule: an unreadable table is UNKNOWN, never empty. Genuine absence (a table that does not exist
yet) is still a real empty — that is a first write, not a loss.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import pyarrow  # noqa: F401
    import duckdb   # noqa: F401
except Exception as e:
    print("SKIP warehouse_accumulate_guard_test: %s" % e)
    sys.exit(0)

_tmp = tempfile.mkdtemp(prefix="wh_guard_")
os.environ["WAREHOUSE_DIR"] = _tmp
os.environ.pop("BUCKET_NAME", None)
os.environ.pop("WAREHOUSE_BUCKET", None)
os.environ.pop("PLACES_BUCKET", None)

import warehouse  # noqa: E402

warehouse._LOCAL_DIR = _tmp

passed = failed = 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ok   %s" % name)
    else:
        failed += 1
        print("  FAIL %s %s" % (name, extra))


TABLE = "guard_probe"
FIELDS = ["id", "v"]
seed = [{"id": str(i), "v": "seed"} for i in range(50)]
# THE FIRST WRITE OF A NEW TABLE reads a table that does not exist yet. DuckDB reports that as
# `IO Error: No files found that match the pattern`, which is NOT one of _is_absent's storage-layer
# phrasings — so a guard that only consulted _is_absent would raise here and break the first write of
# every table in the codebase. Caught by this line before it shipped; keep it first.
warehouse.write_accumulate(TABLE, seed, key=lambda r: r["id"], fields=FIELDS)
ok("the FIRST write of a brand-new table still works (absence != read failure)",
   warehouse.query(TABLE, "SELECT count(*) AS n FROM t")[0]["n"] == 50)

# 1. a TRANSIENT read failure must raise and leave the table untouched
_real_query = warehouse.query


def _boom(name, sql=None, params=None):
    if name == TABLE and sql and "SELECT * FROM t" in sql:
        raise RuntimeError("HTTP 503 slow down (transient)")
    return _real_query(name, sql, params)


warehouse.query = _boom
raised = None
try:
    warehouse.write_accumulate(TABLE, [{"id": "999", "v": "batch"}], key=lambda r: r["id"], fields=FIELDS)
except Exception as e:
    raised = e
warehouse.query = _real_query

ok("a transient read failure RAISES instead of merging against []", raised is not None, raised)
ok("…and says why", raised is not None and "Refusing to merge" in str(raised), str(raised)[:120])
n_after = warehouse.query(TABLE, "SELECT count(*) AS n FROM t")[0]["n"]
ok("…and the table is NOT clobbered (this is the 800-product bug)", n_after == 50, n_after)
ok("…so the batch did not replace the table", n_after != 1, n_after)

# 2. genuine absence is still an empty table — a first write must work
_real_query2 = warehouse.query


def _absent(name, sql=None, params=None):
    if name == "guard_brand_new" and sql and "SELECT * FROM t" in sql:
        raise FileNotFoundError("Path does not exist 'guard_brand_new.parquet'")
    return _real_query2(name, sql, params)


warehouse.query = _absent
err = None
try:
    warehouse.write_accumulate("guard_brand_new", [{"id": "1", "v": "first"}],
                               key=lambda r: r["id"], fields=FIELDS)
except Exception as e:
    err = e
warehouse.query = _real_query2
ok("genuine absence is treated as an empty table, not an error", err is None, err)
ok("…and the first write lands",
   warehouse.query("guard_brand_new", "SELECT count(*) AS n FROM t")[0]["n"] == 1)

# 3. the normal merge path still merges (no regression)
warehouse.write_accumulate(TABLE, [{"id": "0", "v": "updated"}, {"id": "50", "v": "new"}],
                           key=lambda r: r["id"], fields=FIELDS)
rows = {r["id"]: r["v"] for r in warehouse.query(TABLE, "SELECT * FROM t")}
ok("merge replaces the matching key", rows.get("0") == "updated", rows.get("0"))
ok("merge appends the new key", rows.get("50") == "new", rows.get("50"))
ok("merge keeps everything else", len(rows) == 51 and rows.get("25") == "seed", len(rows))

shutil.rmtree(_tmp, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
