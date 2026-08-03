#!/usr/bin/env python3
"""metro_scope_test.py — city_centroid.metro_cities() + doordash_chains.all_stores(metro=...)

Scoping a scrape to "Orlando, FL" by literal city-name match misses every suburb (Windermere, Winter
Garden, ...) that has its own distinct city value in the source data — a real gap found live 2026-08-03
when asked to scope UberEats/DoorDash to a metro-area list. metro_cities() answers "which places are
NEAR this anchor" from the existing free city_centroids reference (radius, not a hand-curated suburb
list); doordash_chains.all_stores(metro=...) is the first real consumer.

Contract this pins:
  1. an anchor's OWN city is in its own scope (distance 0)
  2. a nearby suburb (within radius) is included, a distant one (outside radius) is not
  3. multiple anchors union their scopes, not intersect
  4. all_stores(metro=...) restricts the universe to exactly the scoped city/state pairs, case/whitespace
     insensitive the same way city_centroid normalizes everything else
  5. all_stores() with no metro is unchanged — every store, no filtering

No network, no real Tigris/Census fetch — a local-disk warehouse with a hand-built city_centroids
reference, same isolation discipline as doordash_chains_concurrency_test.py.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def main():
    for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
        os.environ.pop(v, None)
    import warehouse
    warehouse._LOCAL_DIR = tempfile.mkdtemp(prefix="metro_scope_")

    # A tiny fake Census reference: Orlando + two suburbs at known distances, plus an unrelated city
    # far enough away to prove the radius actually excludes something.
    warehouse.write_parquet("city_centroids", [
        {"state": "FL", "city": "orlando", "lat": 28.5383, "lng": -81.3792},
        {"state": "FL", "city": "windermere", "lat": 28.4936, "lng": -81.5326},     # ~10mi from Orlando
        {"state": "FL", "city": "winter garden", "lat": 28.5652, "lng": -81.5862},  # ~15mi from Orlando
        {"state": "FL", "city": "miami", "lat": 25.7617, "lng": -80.1918},          # ~230mi from Orlando
    ], fields=["state", "city", "lat", "lng"])

    import city_centroid as cc
    cc._CACHE.clear(); cc._ALIAS.clear(); cc._STATE_CITIES.clear()   # fresh load, don't trust import order

    print("1. an anchor's own city is in its own scope")
    scope = cc.metro_cities([("Orlando", "FL")], radius_mi=30)
    check("orlando itself is in-scope", ("orlando", "FL") in scope)

    print("\n2. a nearby suburb is included, a distant city is not")
    check("windermere (~10mi) is in-scope", ("windermere", "FL") in scope)
    check("winter garden (~15mi) is in-scope", ("winter garden", "FL") in scope)
    check("miami (~230mi) is NOT in-scope", ("miami", "FL") not in scope)

    print("\n3. a tighter radius excludes what a wider one includes")
    tight = cc.metro_cities([("Orlando", "FL")], radius_mi=5)
    check("radius=5mi excludes windermere (~10mi)", ("windermere", "FL") not in tight)
    wide = cc.metro_cities([("Orlando", "FL")], radius_mi=20)
    check("radius=20mi still includes windermere", ("windermere", "FL") in wide)

    print("\n4. multiple anchors union their scopes")
    union = cc.metro_cities([("Orlando", "FL"), ("Miami", "FL")], radius_mi=30)
    check("union includes both anchors", ("orlando", "FL") in union and ("miami", "FL") in union)
    check("union includes orlando's suburb too", ("windermere", "FL") in union)

    print("\n5. doordash_chains.all_stores(metro=...) restricts the universe correctly")
    import doordash_chains as ddc
    warehouse.write_accumulate("doordash_stores", [
        {"store_id": "s1", "name": "Publix - Orlando", "city": "Orlando", "state": "FL"},
        {"store_id": "s2", "name": "CVS - Windermere", "city": "  Windermere ", "state": "fl"},  # whitespace/case
        {"store_id": "s3", "name": "Target - Miami", "city": "Miami", "state": "FL"},
        {"store_id": "s4", "name": "No City Store", "city": None, "state": None},
    ], key="store_id")

    scoped = ddc.all_stores(log=lambda *_: None, metro=cc.metro_cities([("Orlando", "FL")], radius_mi=30))
    check("scoped universe has exactly the 2 in-scope stores", set(scoped) == {"s1", "s2"}, scoped)
    check("out-of-scope Miami store excluded", "s3" not in scoped)
    check("null-city store excluded (never crashes on it)", "s4" not in scoped)

    unscoped = ddc.all_stores(log=lambda *_: None)
    check("no metro= means every store, unfiltered", set(unscoped) == {"s1", "s2", "s3", "s4"}, unscoped)

    print("\n%d/%d passed" % (len(RAN) - len(FAILED), len(RAN)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
