"""Offline test for representativeness (MOAT R): python representativeness_test.py
Synthetic fact_velocity + outlet_master with a HAND-COMPUTED survey projection + CI, and the
suppression path (too-thin coverage → OBSERVED only). Isolated local warehouse, no network."""
import math
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="rep_")
warehouse._LOCAL_DIR = TMP
import representativeness as rep

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


# map the synthetic source to a state and pin thresholds
rep.SOURCE_STATE = {"t": "ZZ"}
os.environ["REP_MIN_OBS"] = "8"
os.environ["REP_MIN_COVERAGE"] = "0.02"
rep.MIN_OBS = 8
rep.MIN_COVERAGE = 0.02

FV = ["source", "store_id", "product_id", "brand", "week", "implied_units", "confidence"]

# BRAND "Big": observed at 10 stores, each exactly 20 units → mean 20, sd 0, observed_total 200.
# Universe ZZ = 100 outlets. coverage 10/100 = 10% ≥ 2% and n=10 ≥ 8 → PROJECTED = 100×20 = 2000.
# sd=0 → CI width 0 (perfectly precise mean). observed 200, projected 2000.
rows = []
for i in range(10):
    rows.append(dict(source="t", store_id="S%d" % i, product_id="P", brand="Big", week="2026-07-13",
                     implied_units=20.0, confidence=0.8))
# BRAND "Thin": observed at only 3 stores → below MIN_OBS → PROJECTED suppressed, OBSERVED still shown.
for i in range(3):
    rows.append(dict(source="t", store_id="T%d" % i, product_id="P", brand="Thin", week="2026-07-13",
                     implied_units=15.0, confidence=0.8))
warehouse.write_parquet("fact_velocity", rows, fields=FV)

# universe: 100 outlets in ZZ (+ blank-state rows that must be ignored)
uni = [dict(outlet_id="o%d" % i, state="ZZ") for i in range(100)] + [dict(outlet_id="b%d" % i, state="") for i in range(5)]
warehouse.write_parquet("outlet_master", uni, fields=["outlet_id", "state"])

res = rep.build(log=lambda *a: None)
mp = {r["brand"]: r for r in warehouse.query("market_projection", "SELECT * FROM t")}

ok("OBSERVED is deterministic (Big = 200)", mp["Big"]["observed_units"] == 200)
ok("coverage computed (10/100 = 10%)", abs(mp["Big"]["coverage"] - 0.10) < 1e-9)
ok("PROJECTED = universe × per-store mean (2000)", mp["Big"]["projected_units"] == 2000)
ok("Big is labelled projected", mp["Big"]["projected_status"] == "projected")
ok("sd=0 → CI width 0 (exact)", mp["Big"]["ci_low"] == 2000 and mp["Big"]["ci_high"] == 2000)

ok("Thin OBSERVED still shown (45)", mp["Thin"]["observed_units"] == 45)
ok("Thin PROJECTED suppressed (too few obs stores)",
   mp["Thin"]["projected_units"] is None and mp["Thin"]["projected_status"].startswith("suppressed"))
ok("suppression reason cites the obs-store floor", "obs stores" in mp["Thin"]["projected_status"])

# a cell with spread → a real (non-zero) CI, and it's a proper fraction of the projection
rows2 = []
vals = [10, 30, 10, 30, 10, 30, 10, 30, 20, 20]   # mean 20, sd>0
for i, v in enumerate(vals):
    rows2.append(dict(source="t", store_id="V%d" % i, product_id="P", brand="Spread", week="2026-07-13",
                      implied_units=float(v), confidence=0.8))
warehouse.write_parquet("fact_velocity", rows + rows2, fields=FV)
rep.build(log=lambda *a: None)
sp = warehouse.query("market_projection", "SELECT * FROM t WHERE brand='Spread'")[0]
ok("spread cell has a real CI band", sp["ci_high"] > sp["projected_units"] > sp["ci_low"] >= 0)
ok("CI is a sane % of the projection", 0 < sp["ci_pct"] < 100)

cov = {r["state"]: r for r in warehouse.query("coverage_cells", "SELECT * FROM t")}
ok("coverage_cells lands the state roll-up", "ZZ" in cov and cov["ZZ"]["universe_outlets"] == 100)
ok("blank-state universe rows excluded", cov["ZZ"]["universe_outlets"] == 100)

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
