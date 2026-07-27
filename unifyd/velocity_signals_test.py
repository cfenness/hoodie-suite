"""Offline test for velocity signals (MOAT V5): python velocity_signals_test.py
Synthetic fact_velocity with a HAND-COMPUTED matched-cell mover + void estimate, and the partial-week
trap that would fool raw-unit WoW. Isolated local warehouse, no network."""
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="vsig_")
warehouse._LOCAL_DIR = TMP
import velocity_signals as vs

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


FIELDS = ["source", "store_id", "product_id", "brand", "week", "implied_units",
          "oos_events", "confidence"]


def row(brand, store, sku, week, units, oos=0, conf=0.8):
    return dict(source="t", store_id=store, product_id=sku, brand=brand, week=week,
                implied_units=float(units), oos_events=int(oos), confidence=conf)


W1, W2 = "2026-07-06", "2026-07-13"
rows = []
# ── MOVER: brand RISE — 6 cells present in BOTH weeks, each 10→15 units (+50%). Matched-cell WoW.
for i in range(6):
    rows.append(row("Rise", "S%d" % i, "P", W1, 10))
    rows.append(row("Rise", "S%d" % i, "P", W2, 15))
# ── PARTIAL-WEEK TRAP: brand "Steady" — 6 matched cells flat at 20 in both weeks, BUT week 2 also has
# 100 EXTRA cells only-in-w1 (a coverage artifact). Raw-unit WoW would show a huge "crash"; matched-cell
# must show ~0% (only the 6 shared cells count).
for i in range(6):
    rows.append(row("Steady", "T%d" % i, "P", W1, 20))
    rows.append(row("Steady", "T%d" % i, "P", W2, 20))
for i in range(100):
    rows.append(row("Steady", "ONLYW1_%d" % i, "P", W1, 5))   # present only in week 1
# ── VOID: brand "Gappy" — typical 8 units/cell; out of stock at 4 of 10 stores.
for i in range(10):
    rows.append(row("Gappy", "G%d" % i, "P", W2, 8, oos=(1 if i < 4 else 0)))
# ── low-confidence cells must be excluded (below CONF_FLOOR 0.4)
for i in range(6):
    rows.append(row("Noise", "N%d" % i, "P", W1, 100, conf=0.1))
    rows.append(row("Noise", "N%d" % i, "P", W2, 1, conf=0.1))

# ── PARTIAL-WEEK flag: brand "Fade" in W2 (full) and W3 (partial). Observation coverage below makes
# W3 read as fewer calendar days → the W2→W3 mover must be flagged partial (an early read, not a trend).
W3 = "2026-07-20"
for i in range(6):
    rows.append(row("Fade", "F%d" % i, "P", W2, 30))
    rows.append(row("Fade", "F%d" % i, "P", W3, 10))   # looks like -67%, but W3 is partial

warehouse.write_parquet("fact_velocity", rows, fields=FIELDS)

# retail_observations giving the coverage signal: W1 & W2 = 3 calendar days each (comparable),
# W3 = 1 day (partial). Only date/source/store/product matter for the distinct-date count.
obs_rows = []
for d in ("2026-07-06", "2026-07-08", "2026-07-10"):       # week of W1
    obs_rows.append(dict(date=d, source="t", store_id="x", product_id="p"))
for d in ("2026-07-13", "2026-07-15", "2026-07-17"):       # week of W2
    obs_rows.append(dict(date=d, source="t", store_id="x", product_id="p"))
obs_rows.append(dict(date="2026-07-20", source="t", store_id="x", product_id="p"))   # week of W3 — 1 day
warehouse.write_partition("retail_observations", "seed", obs_rows,
                          fields=["date", "source", "store_id", "product_id"])

res = vs.build(log=lambda *a: None)

mv = {r["brand"]: r for r in warehouse.query("signal_movers", "SELECT * FROM t")}
ok("Rise: matched-cell +50%", "Rise" in mv and abs(mv["Rise"]["pct_change"] - 50.0) < 0.1)
ok("Rise matched 6 cells (not the extras)", mv["Rise"]["matched_cells"] == 6)
ok("PARTIAL-WEEK TRAP: Steady ~0% (matched-cell immune to coverage)",
   "Steady" in mv and abs(mv["Steady"]["pct_change"]) < 0.1 and mv["Steady"]["matched_cells"] == 6)
ok("low-confidence brand excluded from movers", "Noise" not in mv)

vd = {r["brand"]: r for r in warehouse.query("signal_voids", "SELECT * FROM t")}
ok("Gappy void: 4 of 10 stores out", "Gappy" in vd and vd["Gappy"]["oos_stores"] == 4 and vd["Gappy"]["stores_seen"] == 10)
ok("Gappy recoverable = 4 stores × 8 units = 32", abs(vd["Gappy"]["est_recoverable_units_wk"] - 32) < 0.5)
ok("Gappy % stores out = 40%", abs(vd["Gappy"]["pct_stores_out"] - 40.0) < 0.1)
ok("no false void for a fully-stocked brand", "Rise" not in vd and "Steady" not in vd)

# partial-week flag: W1→W2 movers are NOT partial (equal coverage); the W2→W3 Fade mover IS partial
ok("comparable weeks NOT flagged partial", mv["Rise"]["partial"] is False)
fade = warehouse.query("signal_movers", "SELECT partial, week FROM t WHERE brand='Fade' AND week='%s'" % W3)
ok("partial current week flagged (the fake-decline guard)", fade and fade[0]["partial"] is True)

ok("both signal tables built", res["movers"] >= 2 and res["voids"] >= 1)

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
