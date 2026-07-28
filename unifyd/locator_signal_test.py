"""Offline test for the locator signal layer: python locator_signal_test.py

Fixture rows only — no warehouse, no network, no credentials. Everything under test is the pure
core of locator_signal (the warehouse-backed `offers()` is a thin read on top of it).

The load-bearing assertions, the ones that encode rules we argued for and would otherwise drift:
  • a DEEP discount that still lands above the local median is NOT a good price (rank by
    percentile, never by % off — the mattress-store problem)
  • a thin pool and a flat market both yield NO band, with a reason — never a guessed claim
  • a STATE-tier source never reports a unit count it couldn't have observed
  • brand render-mode never ships a negative verdict or a "wait for the promo" nudge
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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


print("size / pack parsing")
eq("1.75L", ls.size_ml("Tito's Handmade Vodka 1.75L"), 1750.0)
eq("750ml", ls.size_ml("Tito's Handmade Vodka 750ML"), 750.0)
eq("1 L", ls.size_ml("Ketel One 1 L"), 1000.0)
eq("375ml", ls.size_ml("Buffalo Trace 375 ml"), 375.0)
eq("unsized name", ls.size_ml("Tito's Handmade Vodka"), None)
eq("absurd size rejected", ls.size_ml("Bulk Tank 50000 ml"), None)
eq("pack", ls.pack_count("White Claw 12 pk 12 oz"), 12)
eq("no pack", ls.pack_count("Tito's 750ml"), None)

print("\nshelf vs promo")
eq("promo cheaper wins", ls.shelf_and_promo({"price": 32.99, "promo": 24.99}), (32.99, 24.99))
eq("promo mirrored to price is discarded", ls.shelf_and_promo({"price": 29.99, "promo": 29.99}),
   (29.99, None))
eq("promo above shelf discarded", ls.shelf_and_promo({"price": 19.99, "promo": 24.99}),
   (19.99, None))
eq("promo only becomes shelf", ls.shelf_and_promo({"price": None, "promo": 21.5}), (21.5, None))
eq("no price at all", ls.shelf_and_promo({"price": None, "promo": None}), (None, None))

print("\nunit price normalization (a 1.75L must not pollute a 750ml pool)")
e175, x175 = ls.unit_prices({"name": "Tito's 1.75L", "price": 32.00})
eq("1.75L → per-750 equivalent", e175, 13.71)
e750, x750 = ls.unit_prices({"name": "Tito's 750ml", "price": 19.99})
eq("750ml is its own unit price", e750, 19.99)
ok("the two are comparable and the big bottle is cheaper per unit", e175 < e750)
eq("promo drives the effective unit price",
   ls.unit_prices({"name": "Tito's 750ml", "price": 24.99, "promo": 19.99})[1], 19.99)
eq("unsized name yields no comparable price", ls.unit_prices({"name": "Tito's", "price": 24.99})[0],
   None)

print("\nprice verdict")
POOL = [18.99, 19.49, 19.99, 20.49, 20.99, 21.99, 22.99, 24.99, 25.99, 27.99]
v_cheap = ls.price_verdict(18.49, POOL)
eq("below the whole pool → great", v_cheap["band"], "great")
eq("percentile 0 at the bottom", v_cheap["percentile"], 0.0)
v_mid = ls.price_verdict(21.99, POOL)
eq("mid pool → typical", v_mid["band"], "typical")
v_high = ls.price_verdict(29.99, POOL)
eq("above the whole pool → high", v_high["band"], "high")
eq("median reported", v_mid["median"], 21.49)
ok("delta vs median is signed", ls.price_verdict(18.49, POOL)["delta"] < 0)

print("\n  RULE 1 — rank by percentile, NOT % off")
# A store with an inflated everyday price cuts 35% and still sits in the top third of the pool.
row = {"name": "Tito's 750ml", "price": 39.99, "promo": 25.99}
shelf, promo = ls.shelf_and_promo(row)
_, eff = ls.unit_prices(row)
deep = ls.price_verdict(eff, POOL)
ok("the discount really is deep (>30% off)", (shelf - promo) / shelf > 0.30)
ok("...but the verdict is not a good price", deep["band"] not in ("great", "good"))
eq("it reads as high", deep["band"], "high")

print("\n  suppression — no claim beats a wrong claim")
eq("thin pool → no band", ls.price_verdict(19.99, [19.99, 20.99])["band"], None)
eq("thin pool says why", ls.price_verdict(19.99, [19.99, 20.99])["reason"], "thin-pool")
FLAT = [21.99, 21.99, 22.00, 22.00, 22.05, 21.95]        # uniform pricing (e.g. a control state)
eq("flat market → no band", ls.price_verdict(21.99, FLAT)["band"], None)
eq("flat market says why", ls.price_verdict(21.99, FLAT)["reason"], "flat-market")
eq("no price → no band", ls.price_verdict(None, POOL)["reason"], "no-price")

print("\npromo cadence (wait-or-buy)")
# a two-day promo landing about every 28 days, three months running
hist = [("2026-01-%02d" % d, d in (5, 6)) for d in range(1, 29)] + \
       [("2026-02-%02d" % d, d in (2, 3)) for d in range(1, 29)] + \
       [("2026-03-%02d" % d, d in (1, 2)) for d in range(1, 29)]
sig = ls.promo_signal(hist)
eq("three episodes found", sig["episodes"], 3)
ok("period is about a month (got %s)" % sig["period_days"], 25 <= sig["period_days"] <= 32)
ok("days since last promo counted", sig["days_since"] is not None)
one = ls.promo_signal([("2026-01-%02d" % d, d in (5, 6)) for d in range(1, 29)])
eq("a single promo is an anecdote, not a period", one["period_days"], None)
due = ls.promo_signal(hist, today_index="2026-03-30")
ok("overdue for a cut → likely_soon", due["likely_soon"])
fresh = ls.promo_signal(hist, today_index="2026-03-04")
ok("just cut → not likely_soon", not fresh["likely_soon"])

print("\nstock signal — the instrument tier governs what we may say")
count_row = {"in_stock": True, "qty": 6}
eq("STATE tier reports no count even when qty rides along",
   ls.stock_signal(count_row, "state")["qty"], None)
ok("STATE tier still reports in/out", ls.stock_signal(count_row, "state")["in_stock"])
s = ls.stock_signal(count_row, "count", {"units_per_week": 14.0, "confidence": 0.8})
eq("COUNT tier reports the count", s["qty"], 6)
eq("days of supply", s["days_of_supply"], 3.0)
eq("low supply flips urgency", s["urgency"], "selling-out")
slow = ls.stock_signal({"in_stock": True, "qty": 40}, "count",
                       {"units_per_week": 7.0, "confidence": 0.8})
eq("well stocked → no urgency", slow["urgency"], None)
lowconf = ls.stock_signal(count_row, "count", {"units_per_week": 14.0, "confidence": 0.05})
eq("below the velocity confidence floor → no velocity claim", lowconf["days_of_supply"], None)
eq("...but the observed count still stands", lowconf["qty"], 6)

print("\nbuild + rank")
GEO = {
    ("total-wine", "1"): {"store_name": "Total Wine — Midtown", "city": "Houston", "state": "TX",
                          "address": "1 Main St", "lat": 29.7604, "lng": -95.3698},
    ("specs", "2"): {"store_name": "Spec's — Smith St", "city": "Houston", "state": "TX",
                     "address": "2 Smith St", "lat": 29.7704, "lng": -95.3798},
    ("binnys", "3"): {"store_name": "Far Store", "city": "Katy", "state": "TX",
                      "address": "3 Far Rd", "lat": 29.9, "lng": -95.8},
}
LATEST = [
    {"source": "total-wine", "store_id": "1", "name": "Tito's Handmade Vodka 750ml",
     "brand": "Tito's", "price": 24.99, "promo": 18.99, "qty": 5, "in_stock": True,
     "date": "2026-07-27"},
    {"source": "specs", "store_id": "2", "name": "Tito's Handmade Vodka 750ml",
     "brand": "Tito's", "price": 21.99, "qty": 30, "in_stock": True, "date": "2026-07-27"},
    {"source": "binnys", "store_id": "3", "name": "Tito's Handmade Vodka 750ml",
     "brand": "Tito's", "price": 27.99, "in_stock": True, "date": "2026-07-27"},
]
SERIES = {("total-wine", "1"): [("2026-07-25", 24.99), ("2026-07-26", 24.99),
                                ("2026-07-27", 18.99)]}
built = ls.build_offers(LATEST, POOL, geo=GEO,
                        tiers={"total-wine": "count", "specs": "count", "binnys": "state"},
                        velocity={("total-wine", "1"): {"units_per_week": 14.0, "confidence": 0.8}},
                        series=SERIES, center=(29.7604, -95.3698))
eq("all three offers built", len(built), 3)
eq("cheapest promo ranks first", built[0]["store"], "Total Wine — Midtown")
eq("it is flagged on promo", built[0]["on_promo"], True)
eq("effective price is the promo", built[0]["effective_price"], 18.99)
eq("verdict is a good one", built[0]["verdict"]["band"], "great")
eq("urgency surfaced", built[0]["stock"]["urgency"], "selling-out")
eq("distance computed", built[0]["distance_mi"], 0.0)
ok("far store is farther", built[-1]["distance_mi"] > 5)
eq("STATE-tier store reports no count",
   [o for o in built if o["source"] == "binnys"][0]["stock"]["qty"], None)
ok("scores are populated and ordered", built[0]["score"] >= built[-1]["score"])
eq("price history rides along for the sparkline", len(built[0]["price_history"]), 3)
eq("history is chronological", built[0]["price_history"][0]["date"], "2026-07-25")
eq("stores with no series get an empty history, not a null",
   [o for o in built if o["source"] == "binnys"][0]["price_history"], [])

print("\nrender modes — the brand widget may not ship a negative verdict")
brand = ls.for_render_mode(built, "brand")
bands = [o["verdict"]["band"] for o in brand]
ok("no negative band survives", all(b in ("great", "good", None) for b in bands))
eq("the good one survives", brand[0]["verdict"]["band"], "great")
worst = [o for o in brand if o["verdict"]["reason"] == "suppressed-brand-mode"]
ok("the high-priced store is suppressed, not deleted", len(worst) >= 1)
ok("no wait-for-the-promo nudge in brand mode",
   all(not (o["promo_outlook"] or {}).get("likely_soon") for o in brand))
consumer = ls.for_render_mode(built, "consumer")
ok("consumer mode is untouched", consumer[0]["verdict"]["band"] == "great")

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
