"""Offline test for the Salsify platform recipe: python salsify_test.py

Captured fixtures only — no warehouse, no network, no credentials. Both fixtures are real product
payloads from the two catalogs that prove the recipe is a PLATFORM recipe rather than one scrape:
BBG (a wholesaler publishing SAP attributes) and Sazerac (a supplier publishing a GS1/GDSN item
master). They share nothing but the envelope, which is exactly the point.

The load-bearing assertions, the ones that encode rules that would otherwise silently drift:
  • BBG's DIST ITEM CODE and its own ITEM DESCRIPTION land under those names — not flattened into
    `id` and `brand` the way the superseded bbg_salsify.py did
  • EVERY value of a multi-value property is kept (BBG's US Market Region is ['Virginia','Nevada'];
    a values[0] read halves it)
  • EVERY property of every property set is captured, whatever the catalog calls it — the Sazerac
    GS1 namespace (gtin, ABV, proof, nutrients, TTB COLA id) shares no names with BBG's
  • ABV vs PROOF are not confused across namespaces
  • page 1 of a catalog is `index.json`, never `products/1.json` (which 403s — the old loop dropped
    the first 16 products of every catalog)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import salsify

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
passed = failed = 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ok   %s" % name)
    else:
        failed += 1
        print("  FAIL %s %s" % (name, extra))


def load(n):
    return json.load(open(os.path.join(FIX, n)))


# ── BBG: the wholesaler namespace ────────────────────────────────────────────────────────────────
bbg = load("salsify_bbg_product.json")
cat = dict(salsify.CATALOGS["bbg"], catalog_id="bbg")
row, props = salsify.parse_product(bbg, cat, {"catalog_name": "BBG - Public Catalog - Master"})
pmap = {}
for g, p, l, i, v, n in props:
    pmap.setdefault(p, []).append(v)

print("BBG (wholesaler / SAP namespace)")
ok("dist item code = the SAP Material ID", row["dist_item_code"] == "9414646", row["dist_item_code"])
ok("item description = BBG's own description",
   row["item_description"] == "1000 Stories ABA Chardonnay", row["item_description"])
ok("title kept separately from the description",
   row["title"] == "1000 Stories Chardonnay Bourbon Barrel Aged 750ML", row["title"])
ok("brand = Family, not the description", row["brand"] == "1000 STORIES", row["brand"])
ok("supplier landed", row["supplier"] == "CONCHA Y TORO USA", row["supplier"])
ok("category landed", row["category"] == "Wine - Still Table", row["category"])
ok("size parsed to ml", row["size_ml"] == 750, row["size_ml"])
ok("ABV read from the 'Spirit Proof' property (a Wine row — BBG stores ABV there)",
   row["abv"] == 14.4, row["abv"])
ok("…and that same property does NOT also fill the proof column", row["proof"] is None, row["proof"])

# BBG's "Spirit Proof" is ONE field in MIXED UNITS — verified live across the catalog: beer/wine/sake
# carry an ABV (4.5 / 5 / 16 / 18), spirits carry a PROOF (30 on a Potter's liqueur, 92 / 97 / 118 on
# whiskeys). Reading it as one unit puts a 118% ABV whiskey in the master. Resolve by category.
print("  · the mixed-unit 'Spirit Proof' field")
for cat_, v, want_abv, want_proof in [
        ("Sp - Whiskey", "118", 59.0, 118.0),
        ("Sp - Cordials & Liqueurs", "30", 15.0, 30.0),
        ("Beer - Craft", "4.8", 4.8, None),
        ("Wine - Sake", "18", 18.0, None),
        ("Wine - Still Table", "14.4", 14.4, None)]:
    got = salsify.resolve_alcohol({"spiritproof": [v]}, cat_)
    ok("   %-26s %-5s -> abv %-5s proof %s" % (cat_, v, want_abv, want_proof),
       got == (want_abv, want_proof), got)
ok("   no category, low value  -> ABV", salsify.resolve_alcohol({"spiritproof": ["12"]}, "") == (12.0, None))
ok("   no category, high value -> proof",
   salsify.resolve_alcohol({"spiritproof": ["92"]}, "") == (46.0, 92.0))
ok("   a real ABV property always wins over the ambiguous one",
   salsify.resolve_alcohol({"percentageofalcoholbyvolume": ["40.000"], "spiritproof": ["80"]},
                           "Sp - Whiskey") == (40.0, None))
ok("   nothing to read -> (None, None), never a guess",
   salsify.resolve_alcohol({}, "Sp - Whiskey") == (None, None))
ok("units per case landed", row["units_per_case"] == "12", row["units_per_case"])
ok("UPC recovered from the asset filename", row["sku_upc"] == "", row["sku_upc"])  # this SKU has none
ok("multi-value facet keeps EVERY value",
   pmap.get("US Market Region") == ["Virginia", "Nevada"], pmap.get("US Market Region"))
ok("multi-value facet joined on the row",
   row["market_region"] == "Virginia; Nevada", row["market_region"])
ok("marketing copy captured (dropped entirely by the old parser)",
   any(p == "Brand Description" for _g, p, _l, _i, _v, _n in props))
ok("every image captured, not just the first", row["image_count"] >= 4, row["image_count"])
ok("property capture is non-trivial", len(props) >= 18, len(props))
ok("fingerprint is stable", salsify.parse_product(bbg, cat)[0]["properties_hash"]
   == row["properties_hash"])

# ── Sazerac: the GS1 / GDSN namespace ────────────────────────────────────────────────────────────
saz = load("salsify_sazerac_product.json")
scat = dict(salsify.CATALOGS["sazerac"], catalog_id="sazerac")
srow, sprops = salsify.parse_product(saz, scat, {"catalog_name": "Sazerac"})
snames = {p for _g, p, _l, _i, _v, _n in sprops}

print("\nSazerac (supplier / GS1 namespace — shares no property names with BBG)")
ok("GTIN landed as the UPC", srow["sku_upc"] == "00195893031092", srow["sku_upc"])
ok("brand from brandName", srow["brand"] == "818", srow["brand"])
ok("item description from the GS1 trade-item description",
   "818 Reposado Tequila" in srow["item_description"], srow["item_description"])
ok("ABV is the ABV", srow["abv"] == 40.0, srow["abv"])
ok("proof is the proof (not conflated with ABV)", srow["proof"] == 80.0, srow["proof"])
ok("net content + UOM code -> ml", srow["size_ml"] == 750, srow["size_ml"])
ok("brand owner landed", srow["brand_owner"] == "Sazerac Company Inc.", srow["brand_owner"])
ok("country of origin landed", "Mexico" in srow["country"], srow["country"])
ok("TTB COLA id captured (unmapped property, kept anyway)",
   any("TTB" in v for _g, _p, _l, _i, v, _n in sprops))
ok("nutrients captured as separate values",
   sum(1 for _g, p, _l, _i, _v, _n in sprops if "Nutrient" in p) >= 5)
ok("ingredients captured", "01ENingredientStatement" in snames)
ok("pack dimensions captured", "tradeItemMeasurementsModuletradeItemMeasurementsheight" in snames)
ok("closure type captured", "alcoholProductsClosingTypeOfBottle" in snames)
ok("government warning captured", "01ENwarningCopyDescription" in snames)
ok("GS1 namespace is deep", len(sprops) >= 60, len(sprops))
ok("no BBG property names leak in", not ({"Material ID", "Bottles Per Case"} & snames))

# ── platform invariants ──────────────────────────────────────────────────────────────────────────
print("\nPlatform")
meta = {"build_id": "B", "total_pages": 3, "page_one": {"productGroups": [
    {"products": [{"id": "P1", "slugifiedId": "P1", "slugifiedTitle": "p-one"}]}]}}
ok("list page 1 is served from index.json, never fetched as products/1.json",
   salsify.list_page("http://x", "B", meta, 1) == [("P1", "p-one")])
ok("size: litres", salsify._to_ml("1.75L") == 1750)
ok("size: bare number + GS1 UOM", salsify._to_ml("750", "MLT") == 750)
ok("size: unknown -> None, never a guess", salsify._to_ml("") is None and salsify._to_ml("n/a") is None)
ok("sitemap product urls parse",
   salsify._PROD_URL_RE.findall(
       "<loc>https://sites.salsify.com/a/b/product/NTRW15123-INT/RW700/</loc>")
   == [("NTRW15123-INT", "RW700")])
ok("site pairs parse out of the platform sitemap index",
   salsify._SITE_RE.findall(
       "<loc>https://sites.salsify.com/1cb19d26-0728-4fb1-afe0-3b8c309b6180/"
       "d28396be-a4b8-4891-9533-c40780422895/sitemap_1.xml</loc>")
   == [("1cb19d26-0728-4fb1-afe0-3b8c309b6180", "d28396be-a4b8-4891-9533-c40780422895")])
ok("seeded catalogs are addressable by id",
   set(salsify.seeds()) == {"bbg", "sazerac", "heaven-hill"}
   and set(salsify.seeds(exclude=["bbg"])) == {"sazerac", "heaven-hill"})

# ONE WRITER. salsify_products is merged with write_accumulate (read-modify-write), so two processes
# merging it at once lose each other's rows — observed live: a run journalled 8,200 landed, the table
# held 1,574 afterwards, because `bbg` and `salsify` were separate registry sources and the dispatcher
# gave each its own machine.
import source_registry as _reg
_writers = [s for s in _reg.SOURCES if s.get("enabled") and "salsify_products" in (s.get("tables") or [])]
ok("exactly ONE enabled registry source writes salsify_products",
   len(_writers) == 1, [s["id"] for s in _writers])
ok("…and it is the platform pass", _writers and _writers[0]["id"] == "salsify")
ok("the platform pass covers every seeded catalog, bbg included",
   "pull(catalogs=seeds()," in open(
       os.path.join(os.path.dirname(os.path.abspath(__file__)), "salsify.py")).read())
ok("every seeded catalog has org + site ids",
   all(c.get("org") and c.get("site") for c in salsify.CATALOGS.values()))

# Every writer projects its rows through a `fields` list, so a key that drifts out of the row dict
# becomes a silently-null column rather than an error. Pin all three schemas.
ok("product row schema matches FIELDS exactly", set(row) == set(salsify.FIELDS),
   set(row) ^ set(salsify.FIELDS))
prow = salsify.property_row("bbg", "9414646", props[0], "2026-08-03", 0)
ok("property row schema matches PROP_FIELDS exactly", set(prow) == set(salsify.PROP_FIELDS),
   set(prow) ^ set(salsify.PROP_FIELDS))
crow = salsify.catalog_row("org", "site", now=0)
ok("catalog row schema matches CAT_FIELDS exactly", set(crow) == set(salsify.CAT_FIELDS),
   set(crow) ^ set(salsify.CAT_FIELDS))
ok("an unprobed catalog still lands (listed, not dropped)",
   crow["status"] == "listed" and crow["total_products"] is None)
ok("a seeded catalog keeps its stable id in the directory",
   salsify.catalog_row(salsify.CATALOGS["bbg"]["org"], salsify.CATALOGS["bbg"]["site"],
                       now=0)["catalog_id"] == "bbg")
ok("property values are capped, never dropped",
   len(salsify.property_row("c", "p", ("g", "n", "l", 0, "x" * 9000, ""), "d", 0)["value"]) == 4000)

# ── landing failures must never be silent (the 800-product hole) ──────────────────────────────────
# Live 2026-08-03: two of BBG's 139 chunks failed to merge into salsify_products while their
# append-only property partitions landed. 800 products vanished; the run reported `ok` with no
# warnings, because row-count verify-landing is blind to a hole that later chunks grow past.
print("\nLanding integrity")
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "salsify.py")).read()
ok("_land RETURNS its failures instead of only logging them",
   "def _land(" in _src and "return fails" in _src)
ok("…and the caller collects them", "land_fails.extend(_land(" in _src)
ok("…and they become run warnings, so the run degrades", "warns.extend(land_fails" in _src)
ok("a chunk merge is retried before being called a failure",
   "for attempt in range(3):" in _src and "land failed after 3 tries" in _src)
ok("the run cross-checks FETCHED against LANDED, not just row-count movement",
   "rows did not survive the " in _src
   and "SELECT count(*) AS n FROM t WHERE catalog_id = ?" in _src)

_wh = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "warehouse.py")).read()
ok("write_accumulate no longer treats an unreadable table as empty",
   "Refusing to merge" in _wh and "_is_absent(e)" in _wh)
ok("…and it knows BOTH dialects of absence (storage 404s and DuckDB's no-files)",
   "_NO_FILES" in _wh.split("def write_accumulate")[1].split("def ")[0])
ok("…genuine absence still starts a new table",
   "existing = []                     # genuinely not there yet" in _wh)
ok("…and the read is retried before it is believed",
   'lambda: query(name, "SELECT * FROM t"), "read-for-merge' in _wh)

# ── the sitemap publishes a slug the data route rejects ──────────────────────────────────────────
# Salsify slugifies the HTML-ESCAPED title into sitemap_1.xml, so "Lemonade & Lime" is written
# `Lemonade-andamp-Lime`, while the data route resolves `Lemonade-and-Lime`. Measured on Sazerac:
# 106 of 107 unfetchable products contained `andamp`; 0 of the 7,573 that fetched did.
print("\nSitemap/route slug disagreement")
_ssrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "salsify.py")).read()
ok("a clean slug is used as-is, never rewritten",
   salsify._slug_variants("Wave-Baja-750ml") == ["Wave-Baja-750ml"])
ok("an `andamp` slug is tried as published FIRST, then repaired",
   salsify._slug_variants("Southern-Comfort-Lemonade-andamp-Lime-330ml")
   == ["Southern-Comfort-Lemonade-andamp-Lime-330ml",
       "Southern-Comfort-Lemonade-and-Lime-330ml"])
ok("every occurrence is repaired, not just the first",
   salsify._slug_variants("A-andamp-B-andamp-C")[1] == "A-and-B-and-C")
ok("the repair only fires on a ROUTING rejection (403/404), not any error",
   "if e.code not in (403, 404):" in _ssrc)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
