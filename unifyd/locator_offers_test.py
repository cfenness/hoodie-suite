"""Integration test for locator_signal.offers(): python locator_offers_test.py

locator_signal_test.py covers the math with fixtures. This covers the part fixtures CAN'T:
the actual warehouse SQL — the partitioned retail_observations read, the date window, the
param binding, and the three joins (src_outlets geo, obs_quality_source tier, fact_velocity).
A seeded LOCAL warehouse in a temp dir; no network, no Tigris, no credentials.

Skips cleanly (exit 0) when duckdb isn't importable, so it can't wedge a machine that only
has the stdlib.
"""
import os
import shutil
import sys
import tempfile
import time

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET",
          "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import duckdb                                              # noqa: F401
except ImportError:
    print("duckdb not installed — skipping the warehouse integration test")
    sys.exit(0)

import warehouse

TMP = tempfile.mkdtemp(prefix="locsig_")
warehouse._LOCAL_DIR = TMP
import locator_signal as ls

passed = failed = 0


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print("  ok   %s" % name)
    else:
        failed += 1
        print("  FAIL %s" % name)


def eq(name, got, want):
    ok("%s (got %r)" % (name, got), got == want)


# --- seed ------------------------------------------------------------------------------------
# Two Houston stores + one far store, ten days of observations. Total Wine runs a promo on the
# last two days; the far store keeps an inflated everyday price and cuts it hard.
TODAY = time.strftime("%Y-%m-%d")
DAYS = [time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400 * d)) for d in range(9, -1, -1)]

OBS_FIELDS = ["date", "source", "store", "store_id", "product_id", "upc", "brand", "name",
              "price", "promo", "on_promo", "in_stock", "qty", "stock_level", "is_hemp"]


def obs(date, source, store_id, price, promo=None, qty=None):
    return {"date": date, "source": source, "store": "", "store_id": store_id,
            "product_id": "p1", "upc": "000850001234", "brand": "Tito's",
            "name": "Tito's Handmade Vodka 750ml", "price": price, "promo": promo,
            "on_promo": promo is not None, "in_stock": True, "qty": qty,
            "stock_level": "", "is_hemp": False}


rows = []
for i, d in enumerate(DAYS):
    promo_tw = 18.99 if i >= 8 else None
    rows.append(obs(d, "total-wine", "1", 24.99, promo_tw, qty=(6 if i >= 8 else 40)))
    rows.append(obs(d, "specs", "2", 20.99 + (i % 3) * 0.5, None, qty=34))
    rows.append(obs(d, "binnys", "9", 39.99, 25.99 if i >= 8 else None))
# a couple more priced stores so the reference pool clears MIN_REF
for i, d in enumerate(DAYS[-3:]):
    rows.append(obs(d, "kroger", "3", 22.49))
    rows.append(obs(d, "meijer", "4", 23.99))
# a 1.75L at the SAME store — must normalize, not pollute the 750ml pool
big = obs(DAYS[-1], "specs", "2", 34.99)
big["name"] = "Tito's Handmade Vodka 1.75L"
big["product_id"] = "p2"
rows.append(big)

for d in DAYS:
    warehouse.write_partition("retail_observations", "%s_seed" % d,
                              [r for r in rows if r["date"] == d], OBS_FIELDS)

warehouse.write_parquet("src_outlets", [
    {"source": "total-wine", "store_id": "1", "store_name": "Total Wine — Midtown",
     "address": "2537 Bagby St", "city": "Houston", "state": "TX", "lat": 29.75, "lng": -95.378},
    {"source": "specs", "store_id": "2", "store_name": "Spec's — Smith St",
     "address": "2410 Smith St", "city": "Houston", "state": "TX", "lat": 29.753, "lng": -95.382},
    {"source": "kroger", "store_id": "3", "store_name": "Kroger — Montrose",
     "address": "1035 N Shepherd", "city": "Houston", "state": "TX", "lat": 29.79, "lng": -95.41},
    {"source": "meijer", "store_id": "4", "store_name": "Meijer — Heights",
     "address": "9 Heights Blvd", "city": "Houston", "state": "TX", "lat": 29.80, "lng": -95.40},
    {"source": "binnys", "store_id": "9", "store_name": "Far Neighborhood Liquor",
     "address": "88 Far Rd", "city": "Amarillo", "state": "TX", "lat": 35.22, "lng": -101.83},
])
warehouse.write_parquet("obs_quality_source", [
    {"source": "total-wine", "qual_tier": "count"}, {"source": "specs", "qual_tier": "count"},
    {"source": "kroger", "qual_tier": "state"}, {"source": "meijer", "qual_tier": "state"},
    {"source": "binnys", "qual_tier": "state"},
])
warehouse.write_parquet("fact_velocity", [
    {"source": "total-wine", "store_id": "1", "week": "2026-W30", "implied_units": 14.0,
     "confidence": 0.81},
    {"source": "specs", "store_id": "2", "week": "2026-W30", "implied_units": 4.0,
     "confidence": 0.70},
])

HOUSTON = (29.7604, -95.3698)

# --- the query -------------------------------------------------------------------------------
print("warehouse-backed offers()")
res = ls.offers("Tito's", center=HOUSTON, radius_mi=15, log=lambda m: None)
got = {o["source"]: o for o in res["offers"]}
ok("the SQL ran and returned offers", bool(res["offers"]))
ok("no degrade reported", not res.get("degraded"))
eq("radius excluded the Amarillo store", "binnys" in got, False)
eq("four Houston stores inside the ring", len(res["offers"]), 4)
eq("trade area named off the geo join", res["trade_area"], "Houston, TX area")
ok("reference pool cleared the floor (n=%s)" % res["reference"].get("n"),
   res["reference"].get("n", 0) >= ls.MIN_REF)

tw = got.get("total-wine", {})
eq("promo detected as the effective price", tw.get("effective_price"), 18.99)
eq("everyday price preserved", tw.get("everyday_price"), 24.99)
eq("promo flagged", tw.get("on_promo"), True)
ok("verdict is a good one (got %r)" % (tw.get("verdict") or {}).get("band"),
   (tw.get("verdict") or {}).get("band") in ("great", "good"))
eq("COUNT-tier store carries its on-hand", (tw.get("stock") or {}).get("qty"), 6)
ok("velocity join landed days-of-supply", (tw.get("stock") or {}).get("days_of_supply") is not None)
eq("urgency flagged from qty vs velocity", (tw.get("stock") or {}).get("urgency"), "selling-out")
ok("price history came back for the sparkline", len(tw.get("price_history") or []) >= 8)

kr = got.get("kroger", {})
eq("STATE-tier store reports no count", (kr.get("stock") or {}).get("qty"), None)
ok("...but is still in stock", (kr.get("stock") or {}).get("in_stock"))

ok("ranked — best first", res["offers"][0]["score"] >= res["offers"][-1]["score"])
eq("the promo store ranks first", res["offers"][0]["source"], "total-wine")

print("\nboth formats at ONE store survive (regression: `latest` was keyed by store only)")
# specs store 2 sells a 750ml AND a 1.75L. Keying the latest-observation map by (source, store)
# alone dropped whichever landed first, silently deleting a real offer.
big = ls.offers("Tito's", center=HOUSTON, radius_mi=15, size="1750", log=lambda m: None)
eq("the 1.75L list is its own scope", big["reference_size"], "1.75 L")
ok("specs shows up with its 1.75L", any(o["source"] == "specs" for o in big["offers"]))
eq("every card in the list is that format",
   sorted({o["size_label"] for o in big["offers"]}), ["1.75 L"])
small = ls.offers("Tito's", center=HOUSTON, radius_mi=15, size="750", log=lambda m: None)
ok("and specs still shows its 750ml in the 750 scope",
   any(o["source"] == "specs" for o in small["offers"]))
ok("the two scopes price the same store differently, as they must",
   [o for o in big["offers"] if o["source"] == "specs"][0]["effective_price"]
   != [o for o in small["offers"] if o["source"] == "specs"][0]["effective_price"])
eq("formats are advertised for tabbing", sorted(f["label"] for f in small["formats"]),
   ["1.75 L", "750 ml"])

print("\nUPC lookup + normalization")
by_upc = ls.offers("000850001234", center=HOUSTON, radius_mi=15, log=lambda m: None)
eq("exact UPC finds the same stores", len(by_upc["offers"]), 4)
ref_max = by_upc["reference"].get("max") or 0
ok("the 1.75L did not pollute the pool as a $34.99 outlier (max=%.2f)" % ref_max, ref_max < 30)

print("\nstores with no coordinates are REPORTED, not silently dropped")
warehouse.write_parquet("src_outlets", warehouse.query("src_outlets", "SELECT * FROM t") + [
    {"source": "ghost", "store_id": "G1", "store_name": "Ungeocoded Liquor",
     "address": "", "city": "", "state": "", "lat": None, "lng": None}])
warehouse.write_partition("retail_observations", "%s_ghost" % DAYS[-1],
                          [obs(DAYS[-1], "ghost", "G1", 18.49)], OBS_FIELDS)
g = ls.offers("Tito's", center=HOUSTON, radius_mi=15, log=lambda m: None)
eq("the ungeocoded store is counted", g.get("omitted_no_geo"), 1)
ok("and named in degraded (%r)" % g.get("degraded"), "no coordinates" in (g.get("degraded") or ""))
ok("it does not appear as an offer", all(o["source"] != "ghost" for o in g["offers"]))
ok("the placeable stores still render", len(g["offers"]) >= 4)

print("\ndegrade paths")
empty = ls.offers("nothing-matches-this", center=HOUSTON, log=lambda m: None)
eq("an unknown product returns empty, not an error", empty["offers"], [])
eq("blank query short-circuits", ls.offers("", log=lambda m: None)["offers"], [])
far = ls.offers("Tito's", center=(47.6, -122.3), radius_mi=15, log=lambda m: None)
eq("a location with no observed stores returns empty", far["offers"], [])

print("\nbrand render mode is applied server-side")
b = ls.offers("Tito's", center=HOUSTON, radius_mi=15, mode="brand", log=lambda m: None)
bands = [(o["verdict"] or {}).get("band") for o in b["offers"]]
ok("no negative band leaves the server (%r)" % bands,
   all(x in ("great", "good", None) for x in bands))

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
