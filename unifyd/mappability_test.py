#!/usr/bin/env python3
"""mappability_test.py — the classifier. stdlib only, no warehouse, no network.

`classify()` decides whether an outlet can go on a map and, if not, which resolver fixes it. It is
pure on purpose so the routing rules are testable without touching 1.77M rows.

The anchor case is the one that was measured and missed: a DoorDash row with a city, an EMPTY state,
and no address fell through all three geo passes and was reported as permanently unreachable — while
`doordash_stores` held its state all along. That must route to `backfill`, never to `none`.

    python3 unifyd/mappability_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mappability as M                                       # noqa: E402

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def row(**kw):
    base = {"source": "doordash", "store_id": "1", "city": "", "state": "", "address": "",
            "lat": None, "lng": None, "geo_precision": None}
    base.update(kw)
    return base


def main():
    print("mappability.classify")

    # --- already mappable -------------------------------------------------------------------------
    st, res, _ = M.classify(row(lat=29.76, lng=-95.36, geo_precision="exact"))
    check("rooftop geo is exact", st == "exact" and res is None)
    st, _, _ = M.classify(row(lat=29.76, lng=-95.36, geo_precision="city"))
    check("centroid geo is city-precision", st == "city")
    st, _, _ = M.classify(row(lat="29.76", lng="-95.36", geo_precision="exact"))
    check("string coordinates still count", st == "exact")

    # Null Island must never read as mapped.
    st, res, _ = M.classify(row(lat=0, lng=0, city="Houston", state="TX"))
    check("(0,0) is NOT mappable", st == "blocked" and res == "centroid")
    st, _, _ = M.classify(row(lat=0.0, lng=0.0))
    check("(0.0,0.0) floats also excluded", st == "blocked")

    # --- ordinary routing --------------------------------------------------------------------------
    _, res, _ = M.classify(row(city="Houston", state="TX"))
    check("city+state -> centroid", res == "centroid")
    _, res, _ = M.classify(row(address="123 Main St"))
    check("address -> census", res == "census")
    _, res, _ = M.classify(row(source="ubereats", store_id="9"))
    check("aggregator with nothing else -> fetch", res == "fetch")

    # --- THE ANCHOR: city, no state, sibling has it ------------------------------------------------
    r = row(city="Houston", state="")
    st, res, why = M.classify(r)
    check("city + empty state with NO sibling is unreachable", res == "none", (st, res, why))

    st, res, why = M.classify(r, sibling={"store_id": "1", "city": "Houston", "state": "TX"})
    check("city + empty state WITH a sibling routes to backfill", res == "backfill", (st, res, why))
    check("the reason names the sibling table", "doordash_stores" in why, why)
    check("the reason names the missing field", "state" in why, why)

    # A sibling that adds nothing must not manufacture a resolver.
    _, res, _ = M.classify(row(city="Houston", state=""),
                           sibling={"store_id": "1", "city": "Houston", "state": ""})
    check("an empty sibling field does not route to backfill", res == "none")

    # Backfill must never be preferred over data we already hold.
    _, res, _ = M.classify(row(city="Houston", state="TX"),
                           sibling={"store_id": "1", "city": "Dallas", "state": "TX"})
    check("a complete row ignores the sibling entirely", res == "centroid")

    # A source with no SIBLINGS entry can't backfill, however rich the sibling looks.
    _, res, _ = M.classify(row(source="ca-abc", city="Napa", state=""),
                           sibling={"store_id": "1", "city": "Napa", "state": "CA"})
    check("undeclared source does not backfill", res == "none")

    # --- genuinely unreachable ---------------------------------------------------------------------
    st, res, why = M.classify(row(source="ca-abc"))
    check("nothing at all -> none", res == "none")
    check("the reason names what the scraper must capture", "SCRAPER" in why, why)
    check("and lists the missing fields", "city" in why and "state" in why, why)

    # --- the declaration itself --------------------------------------------------------------------
    check("doordash declares a sibling", "doordash" in M.SIBLINGS)
    check("the sibling carries state", "state" in M.SIBLINGS["doordash"]["fields"])

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
