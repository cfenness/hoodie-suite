#!/usr/bin/env python3
"""doordash_classification_test.py — being found under the alcohol category page is not proof of
being alcohol.

Grounded in a real, live incident (2026-08-01): real Lowe's and Michaels stores landed 4,000-6,000+
items each in doordash_products_full, EVERY ONE tagged department='alcohol', is_alcoholic=True — wood
dowels, steel brackets, ceramic tile, craft supplies. Root cause: full_catalog()'s tree walk enqueues
any category link _is_alcohol() doesn't recognize as an EXCLUDED non-alcohol department (grocery,
household, meat, snack, ...) — a fixed exclude-list can never enumerate every retailer's OTHER
departments (hardware, crafts, home goods, ...), so a walk that strays off the true alcohol section
still gets crawled and landed as if it were on-topic. run()'s _work() then set is_alcoholic purely from
WHICH category page an item came from (dept == "alcohol"), never from what the item actually is.

Fixed: every alcohol-tagged item is now also run through cocktail_taxonomy.classify_beverage() — the
same real, content-based check doordash_naop.py already uses for restaurant menus — and dropped if it
isn't genuinely alcoholic (or a mocktail). This file proves the fix survives real items and rejects
real contamination, using the ACTUAL item names captured live in the incident.

No real network call — full_catalog() is monkeypatched to return a canned mixed batch, same discipline
as the rest of this suite.

    python3 unifyd/doordash_classification_test.py
"""
import os
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

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
    import warehouse
    warehouse._LOCAL_DIR = tempfile.mkdtemp(prefix="dd_classify_")
    import doordash_full as ddf

    # The exact shape of the live incident: a Lowe's-style store whose alcohol-tree walk strayed into
    # hardware/home-goods departments _is_alcohol() didn't know to exclude, alongside a handful of
    # genuinely alcoholic items that SHOULD survive. All tagged department="alcohol" by full_catalog()
    # because that's the only tag it ever applies on this path — the bug is that nothing downstream
    # ever questioned it.
    CONTAMINATED_BATCH = [
        {"name": "Project Source White Rod Flange 2 ct", "price": "$4.98", "image_url": "", "department": "alcohol"},
        {"name": "Madison Mill Round Oak 5/16\" x 36\" Dowel", "price": "$3.28", "image_url": "", "department": "alcohol"},
        {"name": "Simpson Strong-Tie Galvanized Shelving Brackets", "price": "$6.98", "image_url": "", "department": "alcohol"},
        {"name": "Daltile Core Classic True White Glossy Ceramic Wall Tile (6 in x 6 in)", "price": "$0.98", "image_url": "", "department": "alcohol"},
        {"name": "Budweiser Lager Beer Cans (12 fl oz x 12 ct)", "price": "$14.99", "image_url": "", "department": "alcohol"},
        {"name": "Kendall-Jackson Vintner's Reserve Chardonnay (750 ml)", "price": "$12.99", "image_url": "", "department": "alcohol"},
        {"name": "BuzzBallz Pink Lemonsqueezy 30 Proof Cocktail Bottle (1.5 L)", "price": "$18.99", "image_url": "", "department": "alcohol"},
        {"name": "Seedlip Garden 108 Non-Alcoholic Spirit (700 ml)", "price": "$29.99", "image_url": "", "department": "non-alcoholic"},
    ]

    real_full_catalog = ddf.full_catalog
    ddf.full_catalog = lambda store, key, log=print, max_pages=120: (
        CONTAMINATED_BATCH,
        {"store_id": str(store), "source": "", "chain": "", "street": "1 Main St", "city": "Redmond",
         "state": "OR", "zip": "97756", "lat": "44.27", "lon": "-121.17"})
    try:
        print("hardware/craft/home-goods items reached via the alcohol tree are DROPPED, not landed")
        run_id, n_items = ddf.run("doordash", stores=["28012456"], store_names={"28012456": "lowe's redmond"},
                                  log=lambda *a: None)
        rows = warehouse.query("doordash_products_full", "SELECT name, is_alcoholic, bev_category FROM t")
        names = {r["name"] for r in rows}

        check("the dowel was dropped", "Madison Mill Round Oak 5/16\" x 36\" Dowel" not in names, names)
        check("the shelving bracket was dropped", "Simpson Strong-Tie Galvanized Shelving Brackets" not in names, names)
        check("the ceramic tile was dropped", "Daltile Core Classic True White Glossy Ceramic Wall Tile (6 in x 6 in)"
              not in names, names)
        check("the rod flange was dropped", "Project Source White Rod Flange 2 ct" not in names, names)

        print("\ngenuinely alcoholic items found on the SAME page still land, unaffected by the filter")
        check("the beer survived", "Budweiser Lager Beer Cans (12 fl oz x 12 ct)" in names, names)
        check("the wine survived", "Kendall-Jackson Vintner's Reserve Chardonnay (750 ml)" in names, names)
        check("the RTD cocktail survived", "BuzzBallz Pink Lemonsqueezy 30 Proof Cocktail Bottle (1.5 L)" in names,
              names)
        check("the non-alcoholic-department item (already filtered on the way in) still survives",
              "Seedlip Garden 108 Non-Alcoholic Spirit (700 ml)" in names, names)

        check("exactly 4 real items landed out of 8 in the contaminated batch", n_items == 4, n_items)

        print("\nis_alcoholic reflects REAL content classification, not which page an item was found on")
        by_name = {r["name"]: r for r in rows}
        check("the beer is marked is_alcoholic=True with bev_category=beer",
              by_name["Budweiser Lager Beer Cans (12 fl oz x 12 ct)"]["is_alcoholic"] and
              by_name["Budweiser Lager Beer Cans (12 fl oz x 12 ct)"]["bev_category"] == "beer",
              by_name.get("Budweiser Lager Beer Cans (12 fl oz x 12 ct)"))
        check("the wine is marked is_alcoholic=True with bev_category=wine",
              by_name["Kendall-Jackson Vintner's Reserve Chardonnay (750 ml)"]["is_alcoholic"] and
              by_name["Kendall-Jackson Vintner's Reserve Chardonnay (750 ml)"]["bev_category"] == "wine",
              by_name.get("Kendall-Jackson Vintner's Reserve Chardonnay (750 ml)"))
    finally:
        ddf.full_catalog = real_full_catalog

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
