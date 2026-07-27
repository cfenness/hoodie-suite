"""Offline test for velocity calibration (MOAT V4): python velocity_calibrate_test.py
Isolated local warehouse. Proves the CONSERVATION ratio (sales vs restock) and — with a SYNTHETIC
anchor that genuinely overlaps the velocity footprint — the external MAPE/scale-fit math, plus the
HONEST coverage=0 report when there's no overlap. No network."""
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="velcal_")
warehouse._LOCAL_DIR = TMP
import observe
import obs_quality
import velocity
import velocity_calibrate as vc

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


_batch = {}


def obs(source, date, row):
    _batch.setdefault((source, date), []).append(row)


def flush():
    for (source, date), rows in _batch.items():
        observe.record(source, rows, date=date, log=lambda *a: None)


def day(n):
    return "2026-07-%02d" % n


# BrandA at S1: 200→100 (SALE 100) then 100→150 (RESTOCK 50). BrandB at S2: 100→50 (SALE 50).
# => implied sales: A=100, B=50 (restock never counts as sale). restock units total = 50.
# => conservation ratio = sales(150) / restock(50) = 3.0
for d, q in [(6, 200), (7, 100), (8, 150)]:
    obs("velco", day(d), dict(store_id="S1", product_id="A", upc="UA", brand="BrandA",
                              price=20.0, on_promo=False, in_stock=True, qty=q))
for d, q in [(6, 100), (7, 50)]:
    obs("velco", day(d), dict(store_id="S2", product_id="B", upc="UB", brand="BrandB",
                              price=15.0, on_promo=False, in_stock=True, qty=q))
# breadth so velco is 'count' tier (qty cardinality + coverage)
for i in range(20):
    obs("velco", day(6), dict(store_id="S3", product_id="Q%d" % i, brand="Filler",
                              price=9.0, in_stock=True, qty=300 + i))
flush()
obs_quality.build(log=lambda *a: None)
velocity.build(log=lambda *a: None)

# ── conservation ────────────────────────────────────────────────────────────────────────────────
cons = vc.conservation(log=lambda *a: None)
byv = {r["source"]: r for r in cons["by_source"]}
ok("conservation ratio = sales/restock (150/50 = 3.0)", abs(byv["velco"]["ratio"] - 3.0) < 1e-6)
ok("conservation carries sales + restock", byv["velco"]["sales"] == 150 and byv["velco"]["restock"] == 50)

# ── external calibration with a SYNTHETIC overlapping anchor ──────────────────────────────────────
# map velco → market 'ZZ', and stand up an anchor with actuals for the same brand×market×period.
# implied A=100, B=50. anchor A=1000, B=400 → scale=(1400/150)=9.333;
# scaled A=933.3 vs 1000 (6.67%), B=466.7 vs 400 (16.67%) → MAPE=11.67%
vc.SOURCE_STATE = {"velco": "ZZ"}
warehouse.write_parquet("fake_anchor", [
    dict(brand="BrandA", market="ZZ", period="2026-07", actual_units=1000.0),
    dict(brand="BrandB", market="ZZ", period="2026-07", actual_units=400.0),
], fields=["brand", "market", "period", "actual_units"])
vc.ANCHORS = dict(vc.ANCHORS)
vc.ANCHORS["fake_anchor"] = dict(market="ZZ", sql=lambda:
    "SELECT upper(trim(brand)) AS brand_key, market, period, actual_units FROM t")

cal = vc.calibrate("fake_anchor", log=lambda *a: None)
ok("overlap found (2 cells)", cal.get("coverage") == 2)
ok("scale fit = Σactual/Σimplied ≈ 9.333", abs(cal["scale"] - 9.3333) < 0.01)
ok("MAPE computed ≈ 11.7%", abs(cal["mape_pct"] - 11.7) < 0.3)

# ── honest coverage=0 when no overlap (map velco to a market the anchor doesn't cover) ────────────
vc.SOURCE_STATE = {"velco": "QQ"}
cal0 = vc.calibrate("fake_anchor", log=lambda *a: None)
ok("no-overlap reported honestly (coverage 0, status pending)",
   cal0.get("coverage") == 0 and cal0.get("status") == "pending-overlap")
ok("no fabricated MAPE when coverage 0", "mape_pct" not in cal0)

# ── build lands the result table ──────────────────────────────────────────────────────────────
vc.SOURCE_STATE = {"velco": "ZZ"}
r = vc.build(log=lambda *a: None)
rows = warehouse.query("velocity_calibration", "SELECT kind, metric, value FROM t")
ok("velocity_calibration table landed", r["rows"] > 0 and any(x["kind"] == "conservation" for x in rows))

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
