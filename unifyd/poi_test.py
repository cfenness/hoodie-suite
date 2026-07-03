"""Offline tests for open-POI ingestion + enrichment: python poi_test.py
Builds a fixture Foursquare-shaped Parquet, loads it via the real DuckDB path (bbox filter
+ normalize), then runs enrich_orlando end-to-end against a local warehouse. No network."""
import os, shutil, tempfile
import poi, places, warehouse

passed = 0
def ok(name, cond):
    global passed
    print(("ok   " if cond else "FAIL ") + name)
    passed += 1 if cond else 0

# --- fixture FSQ Parquet: 2 in Orlando bbox, 1 in Miami (out) ---
import pyarrow as pa, pyarrow.parquet as pq
fx = os.path.join(tempfile.gettempdir(), "fsq_fixture.parquet")
pq.write_table(pa.table({
    "name": ["The Ravenous Pig", "Tacos El Rey", "Beach Bar"],
    "postcode": ["32789-1234", "32801", "33139"],
    "latitude": [28.59, 28.54, 25.79],
    "longitude": [-81.35, -81.38, -80.13],
    "fsq_category_labels": [["Dining and Drinking > Restaurant > Gastropub"],
                            ["Dining and Drinking > Restaurant > Mexican"],
                            ["Dining and Drinking > Bar"]],
}), fx)

pois, meta = poi.load(source="fsq", uri=fx)
ok("bbox filters out Miami (2 of 3)", meta["count"] == 2)
by = {p["name"]: p for p in pois}
ok("category = leaf label", by["The Ravenous Pig"]["category"] == "Gastropub")
ok("zip trimmed to 5", by["The Ravenous Pig"]["zip"] == "32789")
ok("carries lat/lng + source", by["Tacos El Rey"]["lat"] == 28.54 and by["Tacos El Rey"]["source"] == "fsq-os")

# --- unmapped source raises (degrade-friendly) ---
bad = os.path.join(tempfile.gettempdir(), "bad.parquet")
pq.write_table(pa.table({"foo": [1]}), bad)
try:
    poi.load(source="fsq", uri=bad); ok("load raises on unmapped schema", False)
except Exception:
    ok("load raises on unmapped schema", True)

# --- enrich_orlando end-to-end (local warehouse) ---
os.environ.pop("AWS_ENDPOINT_URL_S3", None); os.environ.pop("BUCKET_NAME", None)
shutil.rmtree(warehouse._LOCAL_DIR, ignore_errors=True)
accts = [
    {"account_id": "B1", "name": "The Ravenous Pig", "zip": "32789", "premise": "on"},
    {"account_id": "B2", "name": "Tacos El Rey", "zip": "32801", "premise": "on"},
    {"account_id": "B3", "name": "Publix Liquors", "zip": "32803", "premise": "off"},
]
warehouse.write_parquet("orlando_accounts", accts, places.FIELDS)
res = places.enrich_orlando(poi_loader=lambda: poi.load(source="fsq", uri=fx))
ok("enrich status ok", res["status"] == "ok")
ok("enrich matched 2 (POI has no Publix)", res["matched"] == 2)

back = {r["name"]: r for r in warehouse.query("orlando_accounts")}
ok("enriched account has category", back["The Ravenous Pig"]["category"] == "Gastropub")
ok("enriched account has geo + poi_source", back["Tacos El Rey"]["lat"] == 28.54 and back["Tacos El Rey"]["poi_source"] == "fsq-os")
ok("unmatched account left intact", back["Publix Liquors"]["category"] is None)

# --- enrich graceful when POI load fails (spine untouched) ---
def boom(): raise RuntimeError("s3 unreachable")
res2 = places.enrich_orlando(poi_loader=boom)
ok("enrich self-reports poi-failed", res2["status"] == "poi-failed" and res2["accounts"] == 3)

TOTAL = 11
print("\n%d/%d passed" % (passed, TOTAL))
raise SystemExit(0 if passed == TOTAL else 1)
