"""Offline tests for the Orlando places connector: python places_test.py
Fixture -> normalize -> filter -> classify -> Parquet -> DuckDB query round-trip. No network."""
import csv, io, os, shutil
import places, warehouse

passed = 0
def ok(name, cond):
    global passed
    print(("ok   " if cond else "FAIL ") + name)
    passed += 1 if cond else 0

H = places.FL_HEADER
def row(**kv):
    r = [""] * len(H)
    idx = {h.lower(): i for i, h in enumerate(H)}
    for k, v in kv.items():
        r[idx[k.replace("_", " ").lower()]] = v
    return r

# Fixture: 5 licenses — 3 Orange (Orlando) of mixed premise, 1 Duval (Jacksonville), 1 dup.
rows = [
    row(dba="The Ravenous Pig", owner_name="RAVENOUS PIG LLC", series="4", modifier="COP",
        location_address_1="565 W FAIRBANKS AVE", location_city="WINTER PARK",
        location_state="FL", location_zip="32789-1234", location_county="Orange",
        license_number="BEV5800001", primary_status="Current", original_licensure_date="03/14/2007"),
    row(dba="Tacos El Rey", owner_name="EL REY INC", series="2", modifier="COP",
        location_address_1="12 CHURCH ST", location_city="ORLANDO", location_state="FL",
        location_zip="32801", location_county="ORANGE", license_number="BEV5800002",
        primary_status="Current"),
    row(dba="Publix Liquors 1123", owner_name="PUBLIX", series="1", modifier="APS",
        location_address_1="900 N MILLS", location_city="ORLANDO", location_state="FL",
        location_zip="32803", location_county="ORANGE", license_number="BEV5800003",
        primary_status="Current"),
    row(dba="Riverside Bar", owner_name="RIVERSIDE LLC", series="4", modifier="COP",
        location_address_1="1 RIVER RD", location_city="JACKSONVILLE", location_state="FL",
        location_zip="32202", location_county="DUVAL", license_number="BEV2600009",
        primary_status="Current"),
    # exact dup of BEV5800002 (same license number) -> should collapse
    row(dba="Tacos El Rey", owner_name="EL REY INC", series="2", modifier="COP",
        location_city="ORLANDO", location_county="ORANGE", location_zip="32801",
        license_number="BEV5800002", primary_status="Current"),
]
def to_csv(rs):
    buf = io.StringIO(); csv.writer(buf).writerows(rs); return buf.getvalue()

# --- unit: classify ---
ok("classify 4COP -> on", places.classify_premise("4", "COP") == "on")
ok("classify 1APS -> off", places.classify_premise("1", "APS") == "off")
ok("classify SFS -> on", places.classify_premise("11", "SFS") == "on")
ok("classify junk -> unknown", places.classify_premise("ZZ", "") == "unknown")

# --- unit: normalize + filter + dedupe ---
recs, warns = places.normalize(rows, H)
ok("normalize maps all rows", len(recs) == 5 and not warns)
ok("normalize name = DBA", recs[0]["name"] == "The Ravenous Pig")
ok("normalize zip trimmed to 5", recs[0]["zip"] == "32789")
market = places.dedupe(places.filter_market(recs))
ok("filter keeps only Orange", all(r["county"] == "ORANGE" for r in market))
ok("dedupe collapses dup license", len(market) == 3)

# --- normalize degradation on bad header ---
_, warns2 = places.normalize([["x", "y"]], ["Foo", "Bar"])
ok("normalize warns on unmapped columns", bool(warns2))

# --- pull() end-to-end with injected fetch, into LOCAL warehouse ---
os.environ.pop("AWS_ENDPOINT_URL_S3", None); os.environ.pop("BUCKET_NAME", None)
shutil.rmtree(warehouse._LOCAL_DIR, ignore_errors=True)
run = places.pull(fetch=lambda eid: to_csv(rows), extracts=["fixtureA"], pulled_at=1700000000)
ok("pull status success", run["status"] == "success")
ok("pull total = 3 Orange dedup", run["total"] == 3)
ok("pull premise breakdown", run["premise"].get("on") == 2 and run["premise"].get("off") == 1)
ok("pull wrote parquet uri", run["uri"] and run["uri"].endswith("orlando_accounts.parquet"))

# --- DuckDB query round-trip ---
allq = warehouse.query("orlando_accounts")
ok("warehouse reads back 3 rows", len(allq) == 3)
onq = warehouse.query("orlando_accounts", "SELECT name FROM t WHERE premise='on' ORDER BY name")
ok("warehouse SQL filter (on-premise = 2)", len(onq) == 2 and onq[0]["name"] == "Tacos El Rey")

# --- pull degraded when nothing in-market ---
run2 = places.pull(fetch=lambda eid: to_csv([rows[3]]), extracts=["fixtureB"], pulled_at=1700000001)
ok("pull degraded on 0 in-market", run2["status"] == "degraded" and any("0 rows in county" in w for w in run2["warnings"]))

# --- enrichment: open POI match ---
poi = [{"name": "The Ravenous Pig", "zip": "32789", "category": "Gastropub",
        "lat": 28.59, "lng": -81.35, "source": "overture"}]
m = places.match_open_poi(market, poi)
ok("open POI match fills category", m == 1 and any(r.get("category") == "Gastropub" for r in market))

# --- enrichment: google disabled + injected client ---
dis = places.enrich_google(market, api_key="")
ok("google disabled without key", dis["status"] == "google-disabled")
res = places.enrich_google(market, _client=lambda n, a: {"place_id": "PID_" + n[:3], "rating": 4.5})
ok("google enrich with injected client", res["matched"] == 3 and market[0]["google_place_id"].startswith("PID_"))

TOTAL = 20
print("\n%d/%d passed" % (passed, TOTAL))
raise SystemExit(0 if passed == TOTAL else 1)
