#!/usr/bin/env python3
"""planogram_build.py — reconstruct a store's ACTUAL planogram from per-product shelf coordinates.

Total Wine's getProduct hands us, for every product in a store, its precise shelf coordinate — the `lisa` code
`08.R.02.03` = Aisle 08 · side R(ight) · Bay 02 · Shelf 03 (and often a 5th = position), plus fixtures like
`COOLERDOOR.06.05`. A product can sit in several spots (`rawLocation` is pipe-joined). So aggregating every
product's coordinates across the store's catalog RE-DRAWS the physical shelf map — no store visit, no photo.

  raw_location "08.R.02.03|COOLERDOOR.06.05"  ->  two placements for one SKU
  group ALL placements by store→aisle→side→bay→shelf, order by position  ->  the planogram.

Feeds the planogram app: from the reconstructed layout you get per-aisle assortment, per-brand shelf share /
facings-proxy (placement count), cooler vs shelf, and adjacency — the shelf intelligence that otherwise needs a
rep with a camera. Kroger (aisle + numOfFacings) and Walmart (aisle) corroborate where they overlap.

    python planogram_build.py --store 920          # rebuild store 920's planogram
    python planogram_build.py --store 920 --aisle 08
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

# lisa: AISLE.SIDE.BAY.SHELF[.POS]  e.g. 08.R.02.03 / 08.R.02.03.2 ; fixtures: COOLERDOOR.06.05, ENDCAP.03.01
_LISA = re.compile(r"^([A-Za-z0-9]+)\.([A-Za-z]?)\.?(\d+)\.(\d+)(?:\.(\d+))?$")
PLACE_FIELDS = ["store_id", "sku", "name", "brand", "category", "size", "fixture", "aisle", "side",
                "bay", "shelf", "position", "is_primary", "lisa"]


def parse_lisa(code):
    """One lisa code -> {fixture/aisle, side, bay, shelf, position}. Handles both AISLE.SIDE.BAY.SHELF and
    fixture forms (COOLERDOOR.06.05). Returns None if it doesn't parse."""
    code = (code or "").strip()
    m = _LISA.match(code)
    if not m:
        # fixture without a side: NAME.BAY.SHELF (e.g. COOLERDOOR.06.05)
        m2 = re.match(r"^([A-Za-z]+)\.(\d+)\.(\d+)$", code)
        if m2:
            return {"fixture": m2.group(1).upper(), "aisle": "", "side": "",
                    "bay": m2.group(2), "shelf": m2.group(3), "position": ""}
        return None
    a, side, bay, shelf, pos = m.groups()
    numeric_aisle = a.isdigit()
    return {"fixture": "" if numeric_aisle else a.upper(), "aisle": a if numeric_aisle else "",
            "side": side or "", "bay": bay, "shelf": shelf, "position": pos or ""}


def placements(source="total_wine_products", store=None, log=print):
    """Every (product, shelf-coordinate) placement across a store's catalog. One product → N placements."""
    where = "raw_location IS NOT NULL AND raw_location <> ''"
    if store:
        where += " AND store_id = '%s'" % store
    try:
        rows = warehouse.query(source, "SELECT store_id, sku, name, brand, category, size, raw_location, "
                                       "bay, shelf, aisle FROM t WHERE " + where)
    except Exception as e:
        log("[planogram] read %s: %s" % (source, str(e)[:80])); return []
    out = []
    for r in rows:
        raw = str(r.get("raw_location") or "")
        primary = True
        for code in raw.split("|"):
            p = parse_lisa(code)
            if not p:
                continue
            out.append({"store_id": str(r.get("store_id") or ""), "sku": str(r.get("sku") or ""),
                        "name": r.get("name") or "", "brand": r.get("brand") or "", "category": r.get("category") or "",
                        "size": r.get("size") or "", "lisa": code.strip(), "is_primary": primary, **p})
            primary = False
    return out


def build(source="total_wine_products", store=None, land=True, log=print):
    places = placements(source, store, log=log)
    if not places:
        log("[planogram] no parseable shelf coordinates (needs Total Wine raw_location — re-pull with the "
            "full-capture parser)."); return []
    if land:
        warehouse.write_accumulate("planogram_placements", places,
                                   key=lambda r: (r["store_id"], r["sku"], r["lisa"]), fields=PLACE_FIELDS)
    # summarize the reconstructed layout
    import collections
    by_store = collections.defaultdict(lambda: collections.defaultdict(set))
    brand_share = collections.defaultdict(collections.Counter)
    for p in places:
        loc = p["fixture"] or ("Aisle %s%s" % (p["aisle"], (" " + p["side"]) if p["side"] else ""))
        by_store[p["store_id"]][loc].add(p["sku"])
        brand_share[(p["store_id"], loc)][p["brand"]] += 1
    for sid, aisles in by_store.items():
        log("[planogram] store %s — %d locations, %d placements" % (sid, len(aisles), len(places)))
        for loc, skus in sorted(aisles.items(), key=lambda kv: -len(kv[1]))[:12]:
            top = brand_share[(sid, loc)].most_common(3)
            log("   %-24s %4d facings-proxy · top: %s" % (loc, len(skus),
                ", ".join("%s(%d)" % (b or "?", n) for b, n in top)))
    log("[planogram] %d placements -> planogram_placements" % len(places))
    return places


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reconstruct a store planogram from per-product shelf coordinates.")
    ap.add_argument("--source", default="total_wine_products")
    ap.add_argument("--store", default=None)
    ap.add_argument("--aisle", default=None)
    a = ap.parse_args(argv)
    places = build(a.source, a.store)
    if a.aisle:
        rows = [p for p in places if p["aisle"] == a.aisle]
        rows.sort(key=lambda p: (p["side"], int(p["bay"] or 0), int(p["shelf"] or 0), int(p["position"] or 0)))
        print("\nAisle %s layout (%d placements):" % (a.aisle, len(rows)))
        for p in rows[:60]:
            print("  %s bay%s shelf%s pos%s  %-40s %s" % (p["side"], p["bay"], p["shelf"], p["position"] or "-",
                                                          (p["name"] or "")[:40], p["brand"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
