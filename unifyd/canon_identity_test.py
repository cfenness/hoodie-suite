"""Tests for canon_identity (S4 slice 2 primitive): python canon_identity_test.py
Proves the coverage-aware overlay — canon merges SKUs unifyd's item_key splits (corroboration lifts),
canon covers some sources and falls back to item_key elsewhere, and it degrades cleanly when the
item_identity table is absent. Local mode, throwaway dir, no network."""
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="canon_id_")
warehouse._LOCAL_DIR = TMP
import canon_identity as ci

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


def seed(rows):
    """rows: list of (source, product_id, canon_item_id)."""
    recs = [dict(source=s, product_id=p, canon_item_id=c) for (s, p, c) in rows]
    warehouse.write_parquet("item_identity", recs, fields=["source", "product_id", "canon_item_id"])


# ── absent table: overlay is inert, everything falls back to item_key ─────────────────────────────
ok("available() False before ingest", ci.available() is False)
ok("identity_map empty before ingest", ci.identity_map() == {})
ok("corroboration empty before ingest", ci.corroboration() == {})
ok("resolve falls back to item_key when absent", ci.resolve("offprem", "A1", "IK123") == "IK123")

# ── seed: Tito's 750ml carried by 3 sources under ONE canon id (item_key split them); a lone item ─
seed([("offprem", "A1", 100), ("kroger", "K1", 100), ("meijer", "M1", 100),   # 3 sources → canon 100
      ("specs", "S9", 200)])                                                    # lone → canon 200
ok("available() True after ingest", ci.available() is True)

idmap = ci.identity_map()
ok("identity_map resolves a covered SKU", idmap.get(("kroger", "K1")) == 100)

# the recall lift, SERVED: corroboration for canon 100 = 3 distinct sources (item_key saw 3 singletons)
corr = ci.corroboration()
ok("canon corroboration lifts to 3 sources", corr[100]["n_sources"] == 3)
ok("corroboration lists the sources", corr[100]["source_list"] == "kroger,meijer,offprem")
ok("lone item stays single-source", corr[200]["n_sources"] == 1)

# ── coverage-aware resolve: canon where covered, item_key fallback elsewhere ──────────────────────
ok("resolve uses canon id where covered", ci.resolve("offprem", "A1", "IKxxx", idmap) == "c:100")
ok("two covered SKUs collapse to ONE identity",
   ci.resolve("offprem", "A1", "IKa", idmap) == ci.resolve("kroger", "K1", "IKb", idmap))
ok("uncovered SKU (TTB) falls back to its item_key",
   ci.resolve("ttb", "T7", "IK_TTB", idmap) == "IK_TTB")
ok("canon ids can't collide with an md5 item_key (c: prefix)",
   ci.resolve("offprem", "A1", "100", idmap) != ci.resolve("nowhere", "Z9", "100", idmap))

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
