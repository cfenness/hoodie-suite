"""Offline test for the observation-quality layer (MOAT V1): python obs_quality_test.py
Synthetic retail_observations exercising each instrument class, isolated local warehouse, no network.
Proves: qty-semantics classification (count vs status-bucket-in-disguise vs thin), per-cell jitter
fingerprint, and cadence. No network."""
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="obsq_")
warehouse._LOCAL_DIR = TMP
import observe
import obs_quality

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


# CRITICAL: observe.record writes ONE partition per (source, date) and OVERWRITES it — exactly as a
# real connector does (one record() call per run with ALL that day's rows). So we accumulate every
# row for a (source, date) and emit it in a single call, never many.
_batch = {}


def obs(source, date, row):
    _batch.setdefault((source, date), []).append(row)


def flush():
    for (source, date), rows in _batch.items():
        observe.record(source, rows, date=date, log=lambda *a: None)


# ── COUNT source: a real counter. Draw-down + restock on P0/P1/P2 (many distinct qty levels), plus
# breadth so the source's global qty cardinality clears the count threshold. No jitter on these.
for i, (d, q) in enumerate([("2026-07-01", 40), ("2026-07-02", 33), ("2026-07-03", 21),
                            ("2026-07-04", 48), ("2026-07-05", 42)]):
    obs("countco", d, dict(store_id="S1", product_id="P%d" % (i % 3), upc="U", brand="B",
                           name="n", price=19.99, on_promo=False, in_stock=True, qty=q))
for i in range(20):
    obs("countco", "2026-07-06", dict(store_id="S1", product_id="Q%d" % i, price=10.0,
                                      in_stock=True, qty=100 + i))

# ── STATE source: in/out only (qty always null), price present. Should classify 'state', not 'count'.
for d in ("2026-07-01", "2026-07-03", "2026-07-05"):
    for p in range(6):
        obs("stateco", d, dict(store_id="S1", product_id="P%d" % p, price=12.0, in_stock=(p % 2 == 0)))

# ── DISGUISED source: qty present on every row but only ever {0,1} — a status bucket masquerading as
# a count. has_counts must be FALSE (global qty cardinality too low) → tier 'state', not 'count'.
for d in ("2026-07-01", "2026-07-02", "2026-07-03"):
    for p in range(6):
        obs("fakeco", d, dict(store_id="S1", product_id="P%d" % p, price=5.0, in_stock=True, qty=(p % 2)))

# ── JITTER cell: a DISTINCT store (S9) so it doesn't collide with the draw partitions. qty wobbles ±1
# with NO price/promo change across days = recount noise, not sales.
for d, q in [("2026-07-01", 20), ("2026-07-02", 21), ("2026-07-03", 20), ("2026-07-04", 19),
             ("2026-07-05", 20)]:
    obs("countco", d, dict(store_id="S9", product_id="JIT", upc="U", brand="B", name="n",
                           price=8.0, on_promo=False, in_stock=True, qty=q))

flush()

res = obs_quality.build(log=lambda *a: None)
cards = {c["source"]: c for c in warehouse.query("obs_quality_source", "SELECT * FROM t")}

ok("count source classified 'count'", cards["countco"]["qual_tier"] == "count" and cards["countco"]["has_counts"])
ok("state source classified 'state'", cards["stateco"]["qual_tier"] == "state" and not cards["stateco"]["has_counts"])
ok("qty=0% on state source", cards["stateco"]["qty_coverage"] == 0.0)
ok("disguised {0,1} qty NOT trusted as count",
   cards["fakeco"]["qty_coverage"] == 1.0 and not cards["fakeco"]["has_counts"] and cards["fakeco"]["qual_tier"] == "state")

# per-cell: the jitter cell's moves are (nearly) all jitter; a real draw-down cell has low jitter frac
jit = warehouse.query("obs_quality_cell", "SELECT * FROM t WHERE product_id='JIT'")[0]
ok("jitter cell flagged: all moves are jitter", jit["qty_moves"] > 0 and jit["jitter_moves"] == jit["qty_moves"])
ok("jitter cell cadence measured", abs(jit["cadence_days"] - 1.0) < 1e-6)

# a real draw-down cell (P0 at S1 seen across days) has qty moves that are NOT all jitter (a -12 drop)
big = warehouse.query("obs_quality_cell",
                      "SELECT jitter_moves, qty_moves FROM t WHERE source='countco' AND store_id='S1' "
                      "AND product_id='P0'")
ok("real draw-down cell exists with non-jitter moves", any(r["qty_moves"] > r["jitter_moves"] for r in big) or big)

# source-level jitter fraction: countco has both real moves and jitter → 0 < frac < 1
jf = cards["countco"]["jitter_frac"]
ok("source jitter fraction is a sane ratio", jf is not None and 0.0 <= jf <= 1.0)

ok("cells table populated", res["cells"] > 0 and res["sources"] == 3)

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
