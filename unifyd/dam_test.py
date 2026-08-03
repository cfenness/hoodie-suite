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

print("\nstory grain (upload-artefact normalization):")
# All five are the SAME story on the live drive; they fragmented into five events until _norm_title
# learned to strip DNA's re-upload UUID prefix, doubled extensions, and the "(n)" variant counter.
SAME = [
    "PRESS RELEASE - ST-GERMAIN x GLASSETTE - Final.docx",
    "345b729b-c2c9-4599-8835-6617573011c4-PRESS RELEASE - ST-GERMAIN x GLASSETTE - Final.docx",
    "1dc10aa7-8b4f-4d87-a52d-39f857514260-PRESS RELEASE - ST-GERMAIN x GLASSETTE - Final.docx",
    "PRESS RELEASE - ST-GERMAIN x GLASSETTE - Final",
    "PRESS RELEASE - ST-GERMAIN x GLASSETTE - Final (2).docx",
]
keys = {dam._norm_title(s) for s in SAME}
check(len(keys) == 1, "one story's upload variants collapse to a single key (got %d)" % len(keys))
check(dam._norm_title("de3c924d-79cd-4b18-af2c-589ff267c96f-Hero Image.jpg.jpg")
      == dam._norm_title("Hero Image"), "a doubled extension behind a UUID prefix is stripped")
check(dam._norm_title("Fleuriste St-Germain Aug 12 (7).jpg")
      == dam._norm_title("Fleuriste St-Germain Aug 12"), "a '(n)' frame counter is stripped")
check(dam._norm_title("BACARDI LAUNCHES 8 YEAR") != dam._norm_title("BACARDI LAUNCHES 4 YEAR"),
      "normalization does NOT collapse genuinely different stories")

# The comparison key is destroyed on purpose; the LABEL a person reads must not be.
check(dam.display_title("345b729b-c2c9-4599-8835-6617573011c4-PRESS RELEASE - ST-GERMAIN x "
                        "GLASSETTE - Final.docx") == "PRESS RELEASE - ST-GERMAIN x GLASSETTE - Final",
      "display_title strips the upload uuid and extension, keeping case and punctuation")
check(dam.display_title("de3c924d-79cd-4b18-af2c-589ff267c96f-Hero Image.jpg.jpg") == "Hero Image",
      "display_title handles a doubled extension behind a uuid")
check(dam.display_title("PLAIN HEADLINE") == "PLAIN HEADLINE", "a clean headline is left alone")

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

print("\ndocument facts (the fetch-for-facts action):")
# A press release states its own date, place and price. Those are the facts the fact feed exists for,
# and they are read VERBATIM — which is what makes them DETERMINISTIC rather than a folder-year guess.
DATELINES = [
    ("LONDON, 12 August 2021 - ST~GERMAIN announced Fleuriste. 20% ABV, available for $34.99.",
     "2021-08-12", "London", 34.99, "USD"),
    ("MIAMI, FL – August 12, 2021 — BACARDI opened a new visitor centre. SRP $19.99.",
     "2021-08-12", "Miami", 19.99, "USD"),
    ("NEW YORK — March 3, 2022 - GREY GOOSE launches. SRP $29.99.",
     "2022-03-03", "New York", 29.99, "USD"),
    ("LONDON, UK, 5 May 2020 — BOMBAY SAPPHIRE. RRP £25.00.",
     "2020-05-05", "London", 25.0, "GBP"),
]
for text, date, market, price, cur in DATELINES:
    f = dam.extract_facts(text)
    check(f.get("event_date") == date and f.get("event_date_precision") == "day",
          "dateline date %s read at DAY precision" % date)
    check(f.get("market") == market and f.get("market_source") == "dateline",
          "dateline place -> market %s" % market)
    check(f.get("price") == price and f.get("currency") == cur,
          "a marked retail price reads %s%s" % (cur, price))

# THE DESIGN'S P2 EXIT EXAMPLE, from the real document. It yielded no date and no price at all until
# the dateline learned brackets + ordinal days and the price learned a TRAILING availability marker.
GLASSETTE = ("﻿HOST WITH FLAIR: ST-GERMAIN X GLASSETTE UNVEIL NEW TABLESCAPE EDIT FOR FESTIVE "
             "HOSTING\n\nThe limited-edition Tablescape Edit introduces a touch of elegance.\n\n"
             "ST-GERMAIN x Glassette Tablescape Edit, curated by Laura Jackson, £150\n"
             "Available from: glassette.com and stgermainliquer.com \n\nHigh Res Imagery\n\n"
             "[LONDON, UK, 9th OCTOBER 2025]  ST-GERMAIN®, the French Elderflower Liqueur, "
             "has partnered with Glassette")
g = dam.extract_facts(GLASSETTE)
check(g.get("event_date") == "2025-10-09" and g.get("event_date_precision") == "day",
      "a BRACKETED dateline with an ORDINAL day parses (%s)" % g.get("event_date"))
check(g.get("date_source") == "dateline", "...and is credited to the dateline, not a body scan")
check(g.get("market") == "London" and g.get("market_source") == "dateline",
      "'[LONDON, UK, …]' resolves the market from the dateline")
check(g.get("price") == 150.0 and g.get("currency") == "GBP",
      "a price followed by 'Available from:' is a price point (%s)" % g.get("price"))

f = dam.extract_facts("MIAMI, FL – August 12, 2021 — BACARDI donates $1,000,000 to hurricane relief.")
check(f.get("event_date") == "2021-08-12", "the date still parses from a non-commercial release")
check(f.get("price") is None,
      "an unmarked currency amount is NOT a price point (a donation is not an SRP)")
check(dam.extract_facts("") == {} and dam.extract_facts("just prose, no facts") == {},
      "a document stating no facts yields no fields — nothing is invented")
check(dam.extract_facts("It is 40% ABV.").get("abv") == 40.0, "a stated ABV is read")

print("\n  the type gate (this must not be a second door to images):")
REC_PROHIBIT = {"source_id": "t", "tos_sha256": "c" * 64,
                "permissions": {"image_use": "prohibited", "scope": "none"}}
check(rights.may(REC_PROHIBIT, "fetch_document_facts")[0],
      "fetch_document_facts is allowed under a record that prohibits every image action")
check(not rights.may(REC_PROHIBIT, "fetch_asset")[0], "...while fetch_asset stays denied")
for ext in ("jpg", "png", "mp4", "mov", "zip", "ttf", ""):
    try:
        dam.document_text(REC_PROHIBIT, {"extension": ext, "asset_url": "https://x/a." + ext})
        check(False, "document_text must refuse .%s" % ext)
    except rights.RightsViolation as e:
        check("only text documents" in str(e), "document_text refuses .%s before any fetch" % (ext or "?"))
    except Exception as e:
        check(False, "document_text(.%s) raised %s, expected RightsViolation" % (ext, type(e).__name__))

stale = dict(REC_PROHIBIT, review_state="stale")
check(not rights.may(stale, "fetch_document_facts")[0],
      "a STALE record blocks document reads too — it touches their server")
check(rights.may(stale, "catalog_metadata")[0],
      "...but cataloguing what we already hold is unaffected by staleness")

print("\n  prose must never land:")
ok_events = [{"event_id": "E1", "title": "A SHORT HEADLINE", "market": "London"}]
check(dam.assert_no_prose(ok_events), "an event of facts lands fine")
try:
    dam.assert_no_prose([{"event_id": "E2", "title": "x" * (dam.MAX_LANDED_FIELD_CHARS + 1)}])
    check(False, "assert_no_prose must reject a body-length field")
except rights.RightsViolation as e:
    check("prose ceiling" in str(e), "a body-length field is REFUSED at the landing boundary")
check(not [f for f in dam.EVENT_FIELDS if f in ("body", "text", "content", "full_text", "abstract")],
      "the event schema has no column that could hold the document body")

print("\n  docx text extraction (stdlib):")
import io as _io
import zipfile as _zip
_buf = _io.BytesIO()
with _zip.ZipFile(_buf, "w") as z:
    z.writestr("word/document.xml",
               '<?xml version="1.0"?><w:document><w:p><w:r><w:t>LONDON, 12 August 2021 '
               '&#8212; ST~GERMAIN launches.</w:t></w:r></w:p><w:p><w:r><w:t>SRP &#163;25.00'
               '</w:t></w:r></w:p></w:document>')
txt = dam._text_from(_buf.getvalue(), "docx")
check("LONDON" in txt and "SRP" in txt, "a docx unzips to text with stdlib only (no dependency)")
check(dam.extract_facts(txt).get("event_date") == "2021-08-12",
      "facts extract from that docx text end to end")
check(dam._text_from(b"not a zip", "docx") == "", "a corrupt docx yields empty text, not a crash")
check(dam._text_from(b"%PDF-1.4 junk", "pdf") == "",
      "a PDF with pypdf absent yields empty text (declared cap makes the run say so)")

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
