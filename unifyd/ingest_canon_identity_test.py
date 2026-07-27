"""Tests for ingest_canon_identity: python ingest_canon_identity_test.py
Verifies canon's item_identity export lands as a stable warehouse table, that two sources sharing a
canon_item_id serve as ONE identity (the recall lift, served), upcs survive as a list, and the
empty-clobber guard protects a populated table. Local mode, throwaway dir, no network."""
import json
import os
import sys
import tempfile

# Force LOCAL mode regardless of shell creds — this test must never touch Tigris.
for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="ingest_ident_")
warehouse._LOCAL_DIR = TMP

import ingest_canon_identity as ing

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


def _jsonl(recs):
    p = os.path.join(TMP, "identity_%d.jsonl" % len(os.listdir(TMP)))
    with open(p, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return p


def _rec(source, product_id, canon_item_id, name="Vodka", upcs=None):
    return {
        "source": source, "product_id": product_id, "canon_item_id": canon_item_id,
        "brand": "Tito's", "product_name": name, "category": "vodka", "size_ml": 750.0,
        "size_raw": "750ml", "upcs": upcs or [], "identity_key": "ik-%d" % canon_item_id,
        "status": "active", "merged_into": None, "match_tier": 0, "method_version": "tier0:upc",
    }


# ── 1) round-trip: the export lands with the frozen schema ──────────────────────────────────────
recs = [
    _rec("offprem", "A1", 100, upcs=["012345678905"]),
    _rec("kroger", "C1", 100, upcs=["012345678905"]),   # same canon item as offprem A1
    _rec("specs", "B1", 200, name="Gin", upcs=["099887766554"]),
]
res = ing.load(_jsonl(recs))
ok("wrote all records", res["rows"] == 3)
ok("distinct canon items", res["items"] == 2)
ok("distinct sources", res["sources"] == 3)
got = warehouse.query(ing.TABLE, "SELECT * FROM t ORDER BY source, product_id")
ok("stable schema", list(got[0].keys()) == ing.FIELDS)

# ── 2) the recall lift, SERVED: two sources → one canon_item_id ──────────────────────────────────
shared = warehouse.query(
    ing.TABLE,
    "SELECT count(DISTINCT canon_item_id) k FROM t WHERE list_contains(upcs, '012345678905')")[0]["k"]
ok("shared UPC serves one identity", shared == 1)
srcs = warehouse.query(
    ing.TABLE, "SELECT count(DISTINCT source) s FROM t WHERE canon_item_id = 100")[0]["s"]
ok("that identity spans two sources", srcs == 2)

# ── 3) upcs survives as a list column ───────────────────────────────────────────────────────────
row = warehouse.query(ing.TABLE, "SELECT upcs FROM t WHERE canon_item_id = 200")[0]
ok("upcs is a list", isinstance(row["upcs"], list) and row["upcs"] == ["099887766554"])

# ── 4) idempotent: re-loading the same export yields the same table ──────────────────────────────
res2 = ing.load(_jsonl(recs))
ok("idempotent re-load", res2["rows"] == 3 and res2["items"] == 2)

# ── 5) empty-clobber guard: a 0-row export must NOT wipe a populated table ───────────────────────
raised = False
try:
    ing.load(_jsonl([]))
except ValueError:
    raised = True
ok("empty export refused (guard)", raised)
ok("table intact after refused empty", warehouse.row_count(ing.TABLE) == 3)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
