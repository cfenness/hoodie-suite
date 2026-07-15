#!/usr/bin/env python3
"""hemp_scan.py — find HEMP / THC BEVERAGE inventory inside the catalogs we already scrape.

Hemp-derived THC/CBD beverages (low-dose seltzers, tonics, sodas, mocktails) are increasingly stocked by
mainstream liquor/grocery, so the fastest hemp coverage is to MINE what we already hold before building new
pulls. Priority = BEVERAGES (not gummies/tinctures/flower). A product qualifies when a cannabinoid signal
(THC/CBD/hemp/delta-9/cannabinoid, or an "N mg" dose) co-occurs with a beverage form (seltzer/soda/tonic/
drink/…), OR it's a known hemp-beverage brand. Lands `hemp_products` (source, name, brand, category, upc,
size_ml, price, image, state, signal) + prints coverage by source. Reusable across every *_products catalog.

    python hemp_scan.py                 # scan all catalogs → hemp_products
    python hemp_scan.py --source offprem_products
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

# cannabinoid signal — the words that mark a hemp/THC product (word-bounded to avoid 'the', 'cbdmt' junk)
_CANNA = re.compile(r"\b(thc|cbd|cbn|cbg|hemp|delta[\s-]?9|delta[\s-]?8|d9|d8|cannabinoid|cannabis|"
                    r"nano[\s-]?thc|live rosin|microdose)\b", re.I)
_DOSE = re.compile(r"\b\d{1,3}\s?mg\b", re.I)                    # "5mg", "10 mg" dosing = strong hemp signal
# beverage FORM — restricts to drinks (the priority), not gummies/tinctures/vapes/flower
_BEV = re.compile(r"\b(seltzer|soda|tonic|drink|beverage|beverages|sparkling|water|shot|shots|elixir|"
                  r"mocktail|cocktail|lemonade|tea|iced tea|kombucha|spritz|fizz|refresher|can|cans|"
                  r"12\s?oz|16\s?oz|8\s?oz|ready[\s-]?to[\s-]?drink|rtd)\b", re.I)
# NON-beverage hemp forms to EXCLUDE even when cannabinoid + a stray form word appears
_NOTBEV = re.compile(r"\b(gummy|gummies|tincture|vape|cartridge|cart|flower|pre[\s-]?roll|edible|"
                     r"chocolate|topical|balm|lotion|capsule|softgel|oil dropper|salve|dog|pet)\b", re.I)
# UNAMBIGUOUS hemp/THC beverage brands — ones that essentially always mean a cannabinoid drink, so the brand
# alone qualifies (many never say 'THC' in the name). Generic words that collide with normal bev-alc names
# (nectar, vibe, mocktails, aura, melo, "day one", "little saints" = adaptogenic-not-THC) are deliberately OUT.
_BRANDS = re.compile(r"\b(cann|br[eē]z|cycling frog|wynk|happi|crescent\s?9|trail magic|uncle arnie|pamos|"
                     r"hi seltzer|keef|artet|mary jones|hometown hero|rebel rabbit|8th wonder|ghost drops|"
                     r"absolute terps|cantrip|top shelf hemp|minny grown|funcky buddha|moon star thc)\b", re.I)

HEMP_FLD = ["source", "name", "brand", "category", "upc", "size_ml", "price", "image", "state", "url", "signal"]


def classify(name, category=""):
    """Is this a HEMP BEVERAGE, and why? Returns a short signal string (or None). Beverage-gated: a cannabinoid
    term must co-occur with a drink form, OR a known brand appears; non-beverage hemp forms are excluded."""
    t = ("%s %s" % (name or "", category or "")).strip()
    if not t or _NOTBEV.search(t):
        return None
    brand = _BRANDS.search(t)
    if brand:                                           # unambiguous hemp-bev brand → qualifies on its own
        return "brand:%s" % brand.group(1).lower()
    canna = _CANNA.search(t)
    if canna and _BEV.search(t):
        return "cannabinoid+beverage"
    if canna and _DOSE.search(t):
        return "cannabinoid+dose"
    return None


def _cols(t):
    cs = set(warehouse.query(t, "SELECT * FROM t LIMIT 1")[0].keys())
    pick = lambda *o: next((c for c in o if c in cs), None)
    return {"name": pick("name", "product_name"), "brand": pick("brand"), "cat": pick("category", "bev_category",
            "type", "cat"), "upc": pick("upc"), "size": pick("size_ml", "size"), "price": pick("price"),
            "image": pick("image", "image_url"), "state": pick("state"), "url": pick("url")}


# broad SQL pre-filter terms — cannabinoid words + brand stems. A dose-only hit ('5mg') always co-occurs with a
# cannabinoid word (the classifier requires canna AND dose), so 'mg' need not be here. This cuts a 1.5M-row
# catalog to the few hundred candidates the Python classifier then decides precisely (it rejects 'canned' etc.).
_PREFILTER = ["thc", "cbd", "cbn", "cbg", "hemp", "delta", "cannabin", "cannabis", "rosin", "microdose", "nano",
              "cann", "brez", "brēz", "cycling frog", "wynk", "happi", "crescent", "trail magic", "uncle arnie",
              "pamos", "hi seltzer", "keef", "artet", "mary jones", "hometown", "rebel rabbit", "8th wonder",
              "ghost drops", "absolute terps", "cantrip", "top shelf", "minny grown", "funcky buddha", "moon star"]


def scan_table(t, log=print):
    c = _cols(t)
    if not c["name"]:
        return []
    sel = ", ".join("%s AS %s" % (c[k], k) for k in c if c[k]) or "%s AS name" % c["name"]
    where = " OR ".join("lower(%s) LIKE '%%%s%%'" % (c["name"], term) for term in _PREFILTER)
    rows = warehouse.query(t, "SELECT %s FROM t WHERE %s" % (sel, where))
    out = []
    for r in rows:
        sig = classify(r.get("name"), r.get("cat"))
        if not sig:
            continue
        # size/price kept as raw STRINGS — sources mix "355", 355.0, "7.5 oz", "$5.99", so a fixed str column
        # is the only type that survives across every catalog (downstream parses as needed).
        out.append({"source": t, "name": r.get("name"), "brand": r.get("brand") or "",
                    "category": r.get("cat") or "", "upc": str(r.get("upc") or ""),
                    "size_ml": ("" if r.get("size") is None else str(r.get("size"))),
                    "price": ("" if r.get("price") is None else str(r.get("price"))),
                    "image": r.get("image") or "", "state": r.get("state") or "",
                    "url": r.get("url") or "", "signal": sig})
    log("  %-24s %5d hemp-beverage of %d" % (t, len(out), len(rows)))
    return out


def scan(sources=None, land=True, log=print):
    tables = sources or sorted({d["name"] for d in warehouse.list_datasets() if d["name"].endswith("_products")})
    all_hits = []
    for t in tables:
        try:
            all_hits.extend(scan_table(t, log=log))
        except Exception as e:
            log("  %-24s skip: %s" % (t, str(e)[:60]))
    if land and all_hits:
        warehouse.write_parquet("hemp_products", all_hits, fields=HEMP_FLD)
    from collections import Counter
    by_src = Counter(h["source"] for h in all_hits)
    by_sig = Counter(h["signal"].split(":")[0] for h in all_hits)
    log("[hemp] %d hemp-beverage products across %d sources -> hemp_products" % (len(all_hits), len(by_src)))
    log("[hemp] by source: %s" % dict(by_src))
    log("[hemp] by signal: %s" % dict(by_sig))
    return all_hits


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find hemp/THC beverages in the catalogs we already scrape.")
    ap.add_argument("--source", default=None, help="one catalog (default: all *_products)")
    ap.add_argument("--no-land", action="store_true")
    a = ap.parse_args(argv)
    hits = scan(sources=[a.source] if a.source else None, land=not a.no_land)
    for h in hits[:25]:
        print("  [%s] %-46s %-14s %s" % (h["source"][:10], (h["name"] or "")[:46], h["signal"], h["upc"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
