"""Offline test for the velocity estimator (MOAT V2): python velocity_test.py
Synthetic retail_observations with a HAND-COMPUTED expected sell-through, isolated local warehouse.
Proves: SALE decrements summed, RESTOCK ignored as sales, NOISE damped (the binny's-58%-jitter guard),
CENSORED long-gap excluded, OOS counted, STATE-tier sources produce no fabricated units, and
confidence in [0,1]. No network."""
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="vel_")
warehouse._LOCAL_DIR = TMP
import observe
import obs_quality
import velocity

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


# batch rows per (source, date) — observe.record overwrites its (source,date) partition (see obs_quality_test)
_batch = {}


def obs(source, date, row):
    _batch.setdefault((source, date), []).append(row)


def flush():
    for (source, date), rows in _batch.items():
        observe.record(source, rows, date=date, log=lambda *a: None)


def day(n):
    return "2026-07-%02d" % n


# ── COUNT source with a KNOWN pattern on cell (S1, HERO), daily, price steady ────────────────────
# qty:  50 → 42 → 30 → 60 → 55            (all same ISO week)
# dq:      -8   -12   +30   -5
# SALE units = 8 + 12 + 5 = 25 ; RESTOCK events = 1 (the +30). No noise (all |dq|>2). No censoring.
for i, q in enumerate([50, 42, 30, 60, 55]):
    obs("countco", day(6 + i), dict(store_id="S1", product_id="HERO", upc="U1", brand="HeroBrand",
                                    price=20.0, on_promo=False, in_stock=True, qty=q))
# ── NOISE cell (S1, WOBBLE): ±1 wobble, no price/promo change → 0 implied units, all damped ───────
for i, q in enumerate([20, 21, 20, 19, 20]):
    obs("countco", day(6 + i), dict(store_id="S1", product_id="WOBBLE", upc="U2", brand="HeroBrand",
                                    price=8.0, on_promo=False, in_stock=True, qty=q))
# ── CENSORED cell (S1, GAP): a big drop across a 12-day gap → excluded from units (a restock could hide) ─
obs("countco", day(6), dict(store_id="S1", product_id="GAP", upc="U3", brand="HeroBrand",
                            price=15.0, on_promo=False, in_stock=True, qty=40))
obs("countco", day(18), dict(store_id="S1", product_id="GAP", upc="U3", brand="HeroBrand",
                             price=15.0, on_promo=False, in_stock=True, qty=5))
# ── OOS cell (S1, OOSP): goes to 0 then recovers ──────────────────────────────────────────────────
for i, q in enumerate([10, 0, 8]):
    obs("countco", day(6 + i), dict(store_id="S1", product_id="OOSP", upc="U4", brand="HeroBrand",
                                    price=12.0, on_promo=False, in_stock=(q > 0), qty=q))
# breadth so countco clears the 'count' tier (global qty cardinality)
for i in range(20):
    obs("countco", day(6), dict(store_id="S2", product_id="B%d" % i, price=9.0, in_stock=True, qty=200 + i))

# ── STATE source: in/out only, qty null → must produce NO implied units (can't invent counts) ────
for i in range(4):
    for p in range(5):
        obs("stateco", day(6 + i), dict(store_id="S1", product_id="P%d" % p, brand="StateBrand",
                                        price=10.0, in_stock=(p + i) % 2 == 0))

flush()
obs_quality.build(log=lambda *a: None)
velocity.build(log=lambda *a: None)

hero = warehouse.query("fact_velocity",
                       "SELECT implied_units, sale_events, restock_events, noise_damped_units, censored_pairs "
                       "FROM t WHERE product_id='HERO'")
ok("SALE decrements summed exactly (8+12+5=25)", len(hero) == 1 and hero[0]["implied_units"] == 25)
ok("RESTOCK not counted as sale", hero[0]["restock_events"] == 1)
ok("HERO had no noise/censoring", hero[0]["noise_damped_units"] == 0 and hero[0]["censored_pairs"] == 0)

wob = warehouse.query("fact_velocity",
                      "SELECT implied_units, noise_damped_units FROM t WHERE product_id='WOBBLE'")
# wobble may be suppressed (conf) or present with 0 units; either way it must contribute NO sales
ok("NOISE cell contributes 0 implied units", (not wob) or wob[0]["implied_units"] == 0)
allrows = warehouse.query("fact_velocity", "SELECT COALESCE(SUM(noise_damped_units),0) n FROM t")
ok("noise WAS damped somewhere (the 58%-jitter guard works)", allrows[0]["n"] > 0)

gap = warehouse.query("fact_velocity", "SELECT implied_units, censored_pairs FROM t WHERE product_id='GAP'")
ok("CENSORED long-gap excluded from units", (not gap) or (gap[0]["implied_units"] == 0 and gap[0]["censored_pairs"] >= 1))

oosp = warehouse.query("fact_velocity", "SELECT oos_events FROM t WHERE product_id='OOSP'")
ok("OOS event captured", oosp and oosp[0]["oos_events"] >= 1)

state = warehouse.query("fact_velocity", "SELECT COUNT(*) n FROM t WHERE source='stateco'")
ok("STATE-tier source produces NO fabricated velocity", state[0]["n"] == 0)

conf = warehouse.query("fact_velocity", "SELECT MIN(confidence) lo, MAX(confidence) hi FROM t")
ok("confidence in [0,1]", 0.0 <= conf[0]["lo"] and conf[0]["hi"] <= 1.0)

mart = warehouse.query("mart_velocity_brand_week",
                       "SELECT brand, implied_units FROM t WHERE brand='HeroBrand'")
ok("brand mart rolls up (HeroBrand ≥ 25 units)", mart and mart[0]["implied_units"] >= 25)
ok("brand mart bounded (no StateBrand — state tier emits nothing)",
   not warehouse.query("mart_velocity_brand_week", "SELECT 1 FROM t WHERE brand='StateBrand'"))

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
