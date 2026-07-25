"""Offline test for the master-quality harness (MOAT M): python master_quality_test.py
Synthetic retail_observations + xwalk with a KNOWN matcher behaviour, so the P/R/F1 math is verified
against hand-computed values — including the under-merge and over-merge failure modes. No network."""
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)
os.environ["MQ_GOLD_PER_CLASS"] = "1000"     # take all synthetic pairs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="mq_")
warehouse._LOCAL_DIR = TMP
import master_quality as mq

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


def seed(products, xwalk):
    """products: list of (source, product_id, upc, brand). xwalk: {(source,product_id): item_key}."""
    import warehouse as w
    obs = [dict(date="2026-07-10", source=s, product_id=p, upc=u, brand=b) for (s, p, u, b) in products]
    w.write_partition("retail_observations", "seed", obs,
                      fields=["date", "source", "product_id", "upc", "brand"])
    xr = [dict(source=s, product_id=p, item_key=k) for (s, p), k in xwalk.items()]
    w.write_parquet("xwalk_source_sku", xr, fields=["source", "product_id", "item_key"])


def run():
    return mq.build(log=lambda *a: None)


# ── PERFECT matcher: same UPC → same item_key; different brand → different key ────────────────────
# 3 same-UPC groups (positives) all merged; distinct-brand products all separate.
prods = [("a", "1", "0001112223334", "Tito's"), ("b", "9", "0001112223334", "Titos"),   # same UPC → key K1
         ("a", "2", "0005556667778", "Grey Goose"), ("b", "8", "0005556667778", "Grey Goose"),  # → K2
         ("a", "3", "0009998887776", "Bombay"), ("b", "7", "0009998887776", "Bombay"),   # → K3
         ("a", "4", "0004443332221", "Jameson"),   # lone (for negatives)
         ("a", "5", "0002221114445", "Patron")]
xw_perfect = {("a","1"):"K1",("b","9"):"K1",("a","2"):"K2",("b","8"):"K2",("a","3"):"K3",("b","7"):"K3",
              ("a","4"):"K4",("a","5"):"K5"}
seed(prods, xw_perfect)
r = run()
ok("perfect matcher: recall = 1.0", r["recall"] == 1.0)
ok("perfect matcher: precision = 1.0", r["precision"] == 1.0)
ok("perfect matcher: F1 = 1.0", r["f1"] == 1.0)
ok("gold has positive + negative pairs", r["n_pos"] >= 3 and r["n_neg"] >= 1)

# ── UNDER-MERGE matcher: every record gets its OWN item_key → same-UPC never merged ──────────────
shutil.rmtree(os.path.join(TMP, "retail_observations"), ignore_errors=True)
xw_under = {k: "U%d" % i for i, k in enumerate(xw_perfect)}
seed(prods, xw_under)
r = run()
ok("under-merge: recall collapses (~0)", r["recall"] == 0.0)
ok("under-merge: precision stays high (no false merges)", r["precision"] is None or r["precision"] == 1.0 or r["fp"] == 0)
ok("under-merge finding surfaced (recall < 0.5)", (r["recall"] or 0) < 0.5)

# ── OVER-MERGE matcher: everything → ONE item_key → merges across brands (false positives) ────────
shutil.rmtree(os.path.join(TMP, "retail_observations"), ignore_errors=True)
xw_over = {k: "ONE" for k in xw_perfect}
seed(prods, xw_over)
r = run()
ok("over-merge: recall = 1.0 (all same-UPC merged)", r["recall"] == 1.0)
ok("over-merge: precision collapses (cross-brand FPs)", r["precision"] is not None and r["precision"] < 0.5)

# ── REGRESSION detection: a good run then a worse run flags the drop ──────────────────────────────
shutil.rmtree(os.path.join(TMP, "retail_observations"), ignore_errors=True)
seed(prods, xw_perfect)
run()                                   # baseline P=R=1.0
shutil.rmtree(os.path.join(TMP, "retail_observations"), ignore_errors=True)
seed(prods, xw_under)                   # recall crashes
r = run()
ok("regression flagged when recall drops vs baseline", "recall" in (r["regressions"] or ""))

# ── gold_matches append-only: each run adds a version ───────────────────────────────────────────
vers = warehouse.query_parts("gold_matches", "SELECT COUNT(DISTINCT version) v FROM t")
ok("gold_matches versioned + append-only", vers and vers[0]["v"] >= 3)

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
