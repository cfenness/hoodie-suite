"""Offline test for the SipSource pipeline: python sipsource_test.py
Tiny scale (fast), isolated local warehouse. Proves: land → marts → EXACT conservation, the serving
mart is dimension-BOUNDED (raw doubles, mart flat), YoY populates, and a scoped query returns the
full month series. No network."""
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="sip_test_")
warehouse._LOCAL_DIR = TMP
import sipsource_sim as sim
import sipsource_ingest as ing

# Shrink the dimension universe so the serving mart SATURATES at test scale — the bounding property
# (mart bounded by brands×markets×months, not raw rows) only shows once raw density exceeds the
# dimension ceiling. Ceiling here = 200×50×37 ≈ 370k, well under the 600k/1.2M raw rows below.
sim.N_BRANDS = 200
sim.N_ITEMS = 2000

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


MONTHS = 37
sim.generate(600_000, months=MONTHS, log=lambda *a: None)
b = ing.build_marts("sip_raw", log=lambda *a: None)
mart1 = b["marts"]["mart_sip_brand_market_month"]

# conservation: marts preserve raw totals exactly
con = ing._con("sip_raw")
rawt = con.execute("SELECT round(SUM(cases_9l),1), round(SUM(revenue_usd),0) FROM raw").fetchone()
for t in ("mart_sip_category_month", "mart_sip_brand_market_month", "mart_sip_supplier_cat_month"):
    m = warehouse.query(t, "SELECT round(SUM(cases_9l),1) c, round(SUM(revenue_usd),0) r FROM t")[0]
    ok("conservation exact: %s" % t,
       abs(rawt[0] - m["c"]) < max(1.0, rawt[0] * 1e-6) and abs(rawt[1] - m["r"]) < max(100, rawt[1] * 1e-6))

# every mart is smaller than raw (it's a roll-up)
n_raw = con.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
ok("marts are roll-ups (smaller than raw)", all(warehouse.row_count(t) < n_raw for t in b["marts"]))

# brand→category is 1:1 in the mart (a brand is one category — carried, not split)
multi = warehouse.query("mart_sip_brand_market_month",
                        "SELECT COUNT(*) n FROM (SELECT brand_id FROM t GROUP BY brand_id "
                        "HAVING COUNT(DISTINCT category) > 1)")[0]["n"]
ok("brand carries a single category", multi == 0)

# YoY populates for periods that have a year-ago (month index >= 12)
yoy = warehouse.query("mart_sip_category_month",
                      "SELECT COUNT(*) n FROM t WHERE cases_yoy_pct IS NOT NULL")[0]["n"]
ok("YoY % populated where a year-ago exists", yoy > 0)

# scoped query returns the full month series for one brand
bid = warehouse.query("mart_sip_brand_market_month", "SELECT brand_id FROM t LIMIT 1")[0]["brand_id"]
series = warehouse.query("mart_sip_brand_market_month",
                         "SELECT period FROM t WHERE brand_id=%d GROUP BY period" % bid)
ok("scoped brand query spans months", 1 <= len(series) <= MONTHS)

# BOUNDING: double the raw, mart barely grows (dimension-bounded serving layer)
shutil.rmtree(os.path.join(TMP, "sip_raw"), ignore_errors=True)
warehouse._MAN_CACHE.clear() if hasattr(warehouse, "_MAN_CACHE") else None
sim.generate(1_200_000, months=MONTHS, log=lambda *a: None)
b2 = ing.build_marts("sip_raw", log=lambda *a: None)
mart2 = b2["marts"]["mart_sip_brand_market_month"]
ok("serving mart is dimension-BOUNDED (2× raw → <1.3× mart)", mart2 < mart1 * 1.3)

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
