"""Exercise dam.py's PURE derivation logic — brand matching, event classification, dates, grain.

Every case here is one the live Bacardi drive actually produced, so this is a regression suite over
real headlines rather than invented ones. Pure stdlib; no network, no warehouse."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dam      # noqa: E402
import rights   # noqa: E402

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


BRANDS = {
    "BACARDÍ": ["bacardí", "bacardi"],
    "GREY GOOSE": ["grey goose"],
    "DEWAR'S": ["dewar's", "dewars", "dewar’s", "john dewar & sons"],
    "MARTINI": ["martini"],
    "BOMBAY SAPPHIRE": ["bombay sapphire", "bombay"],
    "ST~GERMAIN": ["st~germain", "st-germain", "st germain"],
}
REC = {"source_id": "dam-test", "tos_sha256": "b" * 64,
       "permissions": {"image_use": "prohibited", "scope": "none"}}

# ── brand matching ────────────────────────────────────────────────────────────────────────────────
print("brand match:")
b, a, amb = dam.match_brand("JOHN DEWAR & SONS FINE SCOTCH WHISKY EMPORIUM RECEIVES TOP HONORS", BRANDS)
check(b == "DEWAR'S" and a == "john dewar & sons", "the LONGEST matching alias wins")

# The live defect: the text was punctuation-stripped but the aliases were not, so "DEWAR'S®" could
# never match "dewar's" and the headline was credited to whichever brand appeared earlier.
b, _a, _amb = dam.match_brand("BACARDI INTRODUCES NEW DEWAR'S® BAR CONCEPT AT KUALA LUMPUR", BRANDS)
check(b == "DEWAR'S", "an apostrophe/® in the headline still matches its alias (not the earlier brand)")
b, _a, _amb = dam.match_brand("ST~GERMAIN® ELDERFLOWER LIQUEUR INTRODUCES FLEURISTE", BRANDS)
check(b == "ST~GERMAIN", "a tilde in a brand name matches its normalized alias")

check(dam.match_brand("A STORY ABOUT NOTHING IN PARTICULAR", BRANDS)[0] is None,
      "a headline with no brand matches nothing (no fuzzy fallback)")
check(dam.match_brand("BACARDIS OF THE WORLD", BRANDS)[0] is None,
      "matching is word-boundaried — 'BACARDIS' is not 'BACARDI'")

_b, _a, amb = dam.match_brand("THE PERFECT MARTINI COCKTAIL RECIPE", BRANDS)
check(amb is True, "an alias that is also a common word is flagged ambiguous")
_b, _a, amb = dam.match_brand("GREY GOOSE VODKA ANNOUNCES NEW PARTNERSHIP", BRANDS)
check(amb is False, "an unambiguous brand literal is not flagged")

# ── event classification ──────────────────────────────────────────────────────────────────────────
print("\nevent type:")
CASES = [
    # The live misfile this ordering exists to prevent: 'ANNOUNCES NEW ...' matched `launch`, so a
    # personnel announcement landed in a product-event feed as a product launch.
    ("BACARDI LIMITED ANNOUNCES NEW EUROPE REGIONAL PRESIDENT", "leadership"),
    ("BACARDI LIMITED NAMES K.C. KAVANAGH CHIEF GLOBAL COMMUNICATIONS OFFICER", "leadership"),
    ("BACARDI FILES FREEDOM OF INFORMATION ACT (FOIA) REQUEST IN HAVANA CLUB", "corporate_legal"),
    ("BACARDI ADVANCES HAVANA CLUB TRADEMARK CASE IN U.S. DISTRICT COURT", "corporate_legal"),
    ("NEW BACARDÍ® X MAJOR LAZER LIMITED EDITION RUM", "limited_edition"),
    ("BOMBAY SAPPHIRE® LAUNCHES A SERIES OF EXPERIMENTAL TASTE EXPERIENCES", "launch"),
    ("ST~GERMAIN INTRODUCES FLEURISTE, A CREATIVE COLLABORATION", "launch"),
    ("GREY GOOSE VODKA ANNOUNCES NEW PARTNERSHIP VENTURE WITH THE WEEKND", "launch"),
    ("BACARDI INITIATES A 'NO-STRAW' MOVEMENT TO REDUCE WASTE", "sustainability"),
    ("THE USBG NATIONAL LEGACY COCKTAIL COMPETITION, SPONSORED BY BACARDÍ", "cocktail_program"),
    ("PROFILE: FRANCOIS THIBAULT", "other"),
]
for title, want in CASES:
    got = dam.classify_event(title)
    check(got == want, "%-16s <- %s" % (got, title[:58]))

# ── markets and prices ────────────────────────────────────────────────────────────────────────────
print("\nmarket / price:")
check(dam.find_market("GREY GOOSE AND SOHO HOUSE TORONTO CELEBRATE") == "Toronto",
      "a city in the gazetteer is picked up")
check(dam.find_market("MARTINI CELEBRATES THE CAREER OF FELIPE MASSA") is None,
      "a headline with no place name yields no market (no guessing)")
check(dam.find_price("NEW RUM LAUNCHES AT $39.99") == (39.99, "USD"), "a $ price is read verbatim")
check(dam.find_price("AVAILABLE FROM £25 IN THE UK") == (25.0, "GBP"), "a £ price is read verbatim")
check(dam.find_price("NO PRICE HERE") == (None, None), "no currency literal yields no price")

# ── the derivation, end to end ────────────────────────────────────────────────────────────────────
print("\nderive_events:")
ASSETS = [
    # one story, four assets -> ONE event
    {"asset_id": 1, "folder_id": 3906, "title": "GREY GOOSE VODKA ANNOUNCES NEW PARTNERSHIP VENTURE",
     "asset_url": "https://x/1.jpg", "created_on": "2018-04-11T09:21:30.000Z"},
    {"asset_id": 2, "folder_id": 3906, "title": "GREY GOOSE VODKA ANNOUNCES NEW PARTNERSHIP VENTURE.pdf",
     "asset_url": "https://x/2.pdf", "created_on": "2018-04-11T09:21:30.000Z"},
    {"asset_id": 3, "folder_id": 3906, "title": "grey goose vodka announces new partnership venture",
     "asset_url": "https://x/3.jpg", "created_on": "2018-04-11T09:21:30.000Z"},
    {"asset_id": 4, "folder_id": 3906, "title": "GREY GOOSE VODKA ANNOUNCES NEW PARTNERSHIP VENTURE!",
     "asset_url": "https://x/4.jpg", "created_on": "2018-04-11T09:21:30.000Z"},
    # a different story, no year folder
    {"asset_id": 5, "folder_id": 4779, "title": "BACARDÍ LAUNCHES THE GUIDE TO GOING OUT",
     "asset_url": "https://x/5.jpg", "created_on": "2026-02-05T00:00:00.000Z"},
    # an ambiguous-alias match
    {"asset_id": 6, "folder_id": 3906, "title": "THE PERFECT MARTINI COCKTAIL",
     "asset_url": "https://x/6.jpg", "created_on": "2018-04-11T09:21:30.000Z"},
    # no brand at all -> no event
    {"asset_id": 7, "folder_id": 3906, "title": "KENTUCKY DERBY | COCKTAIL COVERAGE",
     "asset_url": "https://x/7.jpg", "created_on": "2018-04-11T09:21:30.000Z"},
]
YEARS = {3906: "2018"}
ev = dam.derive_events(REC, ASSETS, BRANDS, folder_years=YEARS)
by_brand = {e["brand"]: e for e in ev}

check(len(ev) == 3, "3 events from 7 assets (dedupe by story, drop the brand-less one) — got %d" % len(ev))
gg = by_brand["GREY GOOSE"]
check(gg["asset_count"] == 4, "the four assets of one story collapse into one event")
check(gg["source_asset_ids"] == "1,2,3,4", "the event names every contributing asset")
check(gg["event_date"] == "2018-01-01" and gg["event_date_precision"] == "year",
      "the year FOLDER supplies the date at year precision")
check(gg["event_date"] != "2018-04-11",
      "the date is NOT the bulk-upload created_on (every 2018 asset carries 2018-04-11)")

bac = by_brand["BACARDÍ"]
check(bac["event_date"] is None and bac["event_date_precision"] == "unknown",
      "an asset outside a year folder gets NO date rather than an invented one")

print("\nprovenance:")
p_gg = json.loads(gg["field_provenance"])
check(p_gg["brand"] == dam.DETERMINISTIC, "an unambiguous brand match is DETERMINISTIC")
check(p_gg["event_type"] == dam.INFERENCE, "event_type is always INFERENCE")
check("market" not in p_gg, "a field with no value carries no provenance claim")
p_mar = json.loads(by_brand["MARTINI"]["field_provenance"])
check(p_mar["brand"] == dam.INFERENCE,
      "a match on an AMBIGUOUS alias is labelled INFERENCE, not DETERMINISTIC")
check(p_gg.get("brand_alias") == "grey goose", "the matched literal is recorded for audit")

check(len({e["event_id"] for e in ev}) == len(ev), "event ids are unique")
ev2 = dam.derive_events(REC, ASSETS, BRANDS, folder_years=YEARS)
check([e["event_id"] for e in ev] == [e["event_id"] for e in ev2],
      "event ids are STABLE across runs (so accumulate updates rather than duplicates)")

# price only where a price means something
priced = dam.derive_events(REC, [{"asset_id": 9, "folder_id": 3906, "asset_url": "u",
                                  "title": "BACARDÍ DONATES $1,000,000 TO HURRICANE RELIEF"}],
                           BRANDS, folder_years=YEARS)
check(priced[0]["price"] is None,
      "a currency amount in a non-commercial story is not landed as a price point")

print("\nfolder paths / years:")
FOLDERS = [{"id": 3616, "name": "Home", "parent_folder_id": 0, "is_root": 1},
           {"id": 3617, "name": "Images", "parent_folder_id": 3616},
           {"id": 3906, "name": "2018", "parent_folder_id": 3617},
           {"id": 4779, "name": "Media Files", "parent_folder_id": 3616}]
paths = dam.folder_paths(FOLDERS, 3616)
check(paths[3906] == "Home/Images/2018", "a nested folder path is built from parent links")
check(paths[3616] == "Home", "the root path is the root name")
years = dam.year_map(FOLDERS)
check(years == {3906: "2018"}, "only exact 4-digit folder names count as years")

# a cycle must not hang the walk
CYC = [{"id": 1, "name": "a", "parent_folder_id": 2}, {"id": 2, "name": "b", "parent_folder_id": 1}]
dam.folder_paths(CYC, 1)
check(True, "a cyclic parent chain terminates instead of hanging")

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
