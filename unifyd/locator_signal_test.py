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
import statistics
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

print("\nbottle FORMAT bucketing")
eq("1.75L snaps to 1750", ls.size_bucket(1750), 1750)
eq("1.7L is the same format as 1.75L", ls.size_bucket(1700), 1750)
eq("750 is its own format", ls.size_bucket(750), 750)
eq("1L is its own format", ls.size_bucket(1000), 1000)
eq("375 is its own format", ls.size_bucket(375), 375)
eq("an off-format size gets no bucket", ls.size_bucket(900), None)
eq("unparsed size gets no bucket", ls.size_bucket(None), None)
eq("label for display", ls.size_label(1750), "1.75 L")
eq("label for ml", ls.size_label(750), "750 ml")

BAND_ORDER = ["great", "good", "typical", "high"]   # worse = higher index
print("\n  RULE 2 — a pool must be FORMAT-homogeneous, not merely unit-normalized")
# Real shape from the live Orlando pull: large formats are cheaper PER 750ml than small ones.
# Mixing them drags the median down and libels every 750ml as overpriced.
MIXED_ROWS = (
    [{"source": "s", "store_id": "L%d" % i, "name": "X 1.75L", "price": p * (1750 / 750.0)}
     for i, p in enumerate((13.4, 13.8, 14.2, 15.0, 15.4))] +
    [{"source": "s", "store_id": "M%d" % i, "name": "X 1L", "price": p * (1000 / 750.0)}
     for i, p in enumerate((22.0, 23.0, 23.6))] +
    [{"source": "s", "store_id": "S%d" % i, "name": "X 750ml", "price": p}
     for i, p in enumerate((22.9, 23.5, 24.2, 25.0, 26.9))]
)
pools = ls.reference_pools(MIXED_ROWS)
eq("pools split by format", sorted(pools.keys()), [750, 1000, 1750])
eq("the 750 pool has only 750s", len(pools[750]), 5)
flat = [u for v in pools.values() for u in v]
fair_750 = 23.5                      # a mid-of-market 750ml
mixed_verdict = ls.price_verdict(fair_750, flat)
fair_verdict = ls.price_verdict(fair_750, pools[750])
# The claim under test is the PENALTY, not a particular band: the identical bottle at the
# identical price must score strictly worse once large formats are allowed into its pool. (A band
# assertion would depend on how extreme the fixture is; live Orlando data — $13.37 for a 1.75L vs
# $27.26 for a 375ml — is far more extreme than this and pushes it all the way to "high".)
ok("a mixed pool penalises the identical bottle (mixed pct %.2f > own-format pct %.2f)"
   % (mixed_verdict["percentile"], fair_verdict["percentile"]),
   mixed_verdict["percentile"] > fair_verdict["percentile"])
ok("...costing it at least a band (%r vs %r)" % (mixed_verdict["band"], fair_verdict["band"]),
   BAND_ORDER.index(mixed_verdict["band"]) > BAND_ORDER.index(fair_verdict["band"]))
ok("because the mixed median is dragged below the real 750 median (%.2f < %.2f)"
   % (mixed_verdict["median"], fair_verdict["median"]),
   mixed_verdict["median"] < fair_verdict["median"])

print("\n  a product with no parseable size gets no band, and says so")
nosize = ls.build_offers([{"source": "s", "store_id": "1", "name": "Tito's Handmade Vodka",
                           "price": 21.99, "in_stock": True, "date": "2026-07-27"}],
                         pools)
eq("no format ⇒ no band", nosize[0]["verdict"]["band"], None)
eq("...and it says why", nosize[0]["verdict"]["reason"], "no-size")

print("\nstore-name decoding (the feeds send it encoded)")
eq("percent-encoded ampersand", ls.clean_store_name("Abc Fine Wine %26 Spirits"),
   "Abc Fine Wine & Spirits")
eq("html entity", ls.clean_store_name("Total Wine &amp; More"), "Total Wine & More")
eq("whitespace tidied", ls.clean_store_name("  ABC   Fine  "), "ABC Fine")
ok("a truncated feed name still keys to the same store",
   ls._name_key("Abc Fine Wine %26 Spirits Oran") == ls._name_key("ABC Fine Wine & Spirits"))

print("\nDEDUPE — one card per physical store PER FORMAT")
DUPES = [
    # the same ABC store, 1.75L, seen by two aggregators — one duplicate
    {"source": "ubereats", "store_id": "u1", "name": "Tito's Handmade Vodka (1.75 L)",
     "price": 31.20, "in_stock": True, "date": "2026-07-27"},
    {"source": "postmates", "store_id": "p1", "name": "Tito's Handmade Vodka (1.75 L)",
     "price": 31.20, "in_stock": True, "date": "2026-07-26"},
    # the same Total Wine store at TWO formats — NOT duplicates
    {"source": "ubereats", "store_id": "u2", "name": "Tito's Handmade Vodka (750 ml)",
     "price": 23.49, "in_stock": True, "date": "2026-07-27"},
    {"source": "postmates", "store_id": "p2", "name": "Tito's Handmade Vodka (1 L)",
     "price": 31.49, "in_stock": True, "date": "2026-07-27"},
]
DUPE_GEO = {
    ("ubereats", "u1"): {"store_name": "Abc Fine Wine %26 Spirits Oran", "lat": 28.50, "lng": -81.40},
    ("postmates", "p1"): {"store_name": "ABC Fine Wine & Spirits", "lat": 28.5001, "lng": -81.4001},
    ("ubereats", "u2"): {"store_name": "Total Wine & More", "lat": 28.46, "lng": -81.43},
    ("postmates", "p2"): {"store_name": "Total Wine & More", "lat": 28.46, "lng": -81.43},
}
d = ls.build_offers(DUPES, pools, geo=DUPE_GEO)
names = sorted((o["store"], o["size_label"]) for o in d)
eq("four rows collapse to three offers", len(d), 3)
ok("the ABC duplicate merged once (%r)" % (names,),
   sum(1 for n, _ in names if n.startswith("ABC") or n.startswith("Abc")) == 1)
ok("both Total Wine FORMATS survive — they are different products, not dupes",
   sorted(s for n, s in names if n == "Total Wine & More") == ["1 L", "750 ml"])
abc = [o for o in d if "bc" in o["store"]][0]
eq("the store name is decoded for display", abc["store"], "Abc Fine Wine & Spirits Oran")
ok("the merged card records the other source it was seen on", "postmates" in (abc["also_seen"] or []))
eq("the fresher observation won", abc["observed_on"], "2026-07-27")

print("\n  and a duplicate no longer double-votes in the pool")
same_store = [{"source": "s", "store_id": "A", "name": "X 750ml", "price": p}
              for p in (20.0, 20.0, 20.0, 20.0, 30.0)]
eq("five scrapes of one store are ONE vote", len(ls.reference_pools(same_store)[750]), 1)
two_stores = same_store + [{"source": "s", "store_id": "B", "name": "X 750ml", "price": 25.0}]
eq("a second store adds a second vote", len(ls.reference_pools(two_stores)[750]), 2)
ok("and offers are deduped before ranking, so a mirrored feed can't take two slots",
   len(ls.dedupe(d)) == len(d))

print("\n  RULE 3b — the pool counts STORES, not scrapes")
# Cadence must not masquerade as consensus. One store scraped 30 times is one opinion.
CADENCE = ([{"source": "daily", "store_id": "A", "name": "X 750ml", "price": 26.99}] * 30 +
           [{"source": "rare", "store_id": "B", "name": "X 750ml", "price": 19.99}] +
           [{"source": "rare", "store_id": "C", "name": "X 750ml", "price": 20.99}] +
           [{"source": "rare", "store_id": "D", "name": "X 750ml", "price": 21.99}])
pooled = ls.reference_pools(CADENCE)[750]
eq("33 observations from 4 stores make a pool of 4", len(pooled), 4)
ok("the daily-scraped store gets ONE vote, not 30", pooled.count(26.99) == 1)
raw = sorted(u for r in CADENCE for u in [ls.unit_prices(r)[1]])
ok("pooling raw scrapes would have put the median at the over-scraped store (%.2f)"
   % statistics.median(raw), statistics.median(raw) == 26.99)
ok("...one-vote-per-store puts it where the market actually is (%.2f)"
   % statistics.median(pooled), statistics.median(pooled) < 26.99)
one_store = ls.reference_pools([{"source": "s", "store_id": "A", "name": "X 750ml",
                                 "price": 20.0 + i} for i in range(20)])[750]
eq("20 scrapes of ONE store is a pool of one — MIN_REF now means what it says", len(one_store), 1)
eq("no band on a one-store pool", ls.price_verdict(21.0, one_store)["reason"], "thin-pool")
eq("a store's own vote is its median, robust to a one-day spike",
   ls.reference_pools([{"source": "s", "store_id": "A", "name": "X 750ml", "price": p}
                       for p in (22.0, 22.0, 22.0, 40.0)])[750], [22.0])

print("\n  RULE 2b — a ranked list may not MIX formats")
# Within-format percentiles are not comparable across formats. Live proof: a 375ml at $13.63 was
# the cheapest 375ml in Orlando, scored "great", and outranked a 750ml at $23.49 — while being far
# worse value per drop. So a list is scoped to one format, chosen by deepest pool.
SCOPE_POOLS = {750: [22.9, 23.5, 24.2, 25.0, 26.9], 375: [27.5, 28.0, 28.4, 29.0]}
SCOPE_ROWS = [
    {"source": "a", "store_id": "1", "name": "X 375ml", "price": 13.63, "in_stock": True,
     "date": "2026-07-27"},
    {"source": "b", "store_id": "2", "name": "X 750ml", "price": 23.49, "in_stock": True,
     "date": "2026-07-27"},
]
sc = ls.build_offers(SCOPE_ROWS, SCOPE_POOLS)
small = [o for o in sc if o["size_bucket"] == 375][0]
big = [o for o in sc if o["size_bucket"] == 750][0]
eq("the 375ml really does score well IN ITS OWN pool", small["verdict"]["band"], "great")
ok("...and would outrank the 750ml if formats were mixed", small["score"] >= big["score"])
ok("which is exactly why it is a different list: per-drop it is far worse value (%.2f vs %.2f)"
   % (small["unit_price"], big["unit_price"]), small["unit_price"] > big["unit_price"])

print("\nSTALENESS — an old reading may be shown, never asserted")
FRESH = {"source": "s", "store_id": "1", "name": "X 750ml", "price": 19.99, "qty": 8,
         "in_stock": True, "date": "2026-07-27"}
OLD = dict(FRESH, store_id="2", date="2026-06-01", price=21.99)
POOL750 = {750: [19.5, 20.5, 21.5, 22.5, 23.5]}
b = ls.build_offers([FRESH, OLD], POOL750, tiers={"s": "count"}, today="2026-07-28")
fr = [o for o in b if o["store_id"] == "1"][0]
ol = [o for o in b if o["store_id"] == "2"][0]
eq("fresh observation is one day old", fr["observed_days_ago"], 1)
ok("fresh gets a real verdict (%r)" % fr["verdict"]["band"], fr["verdict"]["band"] is not None)
eq("fresh reports its count", fr["stock"]["qty"], 8)
eq("stale observation age carried", ol["observed_days_ago"], 57)
eq("stale gets NO band", ol["verdict"]["band"], None)
eq("...and says why", ol["verdict"]["reason"], "stale")
eq("stale asserts no stock", ol["stock"]["in_stock"], False)
eq("stale reports no count", ol["stock"]["qty"], None)
ok("stale is flagged as such", ol["stock"]["stale"])
ok("but the price we saw is still shown", ol["effective_price"] == 21.99)

print("\nQTY SANITY — a count must be a plausible count")
neg = ls.stock_signal({"in_stock": True, "qty": -232}, "count", age=0)
eq("a negative count is not stock (sevennow lands -232)", neg["qty"], None)
huge = ls.stock_signal({"in_stock": True, "qty": 999999}, "count", age=0)
eq("an absurd count is rejected too", huge["qty"], None)
zero = ls.stock_signal({"in_stock": True, "qty": 0}, "count", age=0)
eq("zero is a legitimate count — out of stock", zero["qty"], 0)
eq("...and it means not in stock", zero["in_stock"], False)
good = ls.stock_signal({"in_stock": True, "qty": 12}, "count", age=0)
eq("a plausible count passes", good["qty"], 12)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
