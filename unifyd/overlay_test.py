"""Offline test for the whole Overlay pipeline: python overlay_test.py

Runs every stage's self-test (mapping, detector registry, match, market read, xlsx writer, the
orchestrator) plus the cross-module invariants that no single module can check on its own — the
ones that would let the page say something it can't back up. No network, no DuckDB, no warehouse.
"""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import overlay
import overlay_detect
import overlay_map
import overlay_market
import overlay_match
import xlsx_write

passed = failed = 0


def ok(name, cond, detail=""):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name + (("  — %s" % detail) if not cond and detail else ""))
    passed += bool(cond)
    failed += not cond


# ── every module's own self-test ───────────────────────────────────────────────────────────────
for mod in (overlay_map, overlay_detect, overlay_match, overlay_market, xlsx_write, overlay):
    try:
        mod._selftest()
        ok("%s self-test" % mod.__name__, True)
    except Exception as e:                                    # noqa: BLE001 — report, don't abort
        ok("%s self-test" % mod.__name__, False, "%s: %s" % (type(e).__name__, e))


# ── cross-module invariants ────────────────────────────────────────────────────────────────────
CSV = ("Item Code,Item Description,Brand,UPC,Size,ABV,Case Price,MY_MARGIN_PCT,Rep Email\n"
       "100234,TITOS HANDMADE TEXAS VODKA,TITO HANDMADE,619947000020,750 ML,40,$21.99,0.31,a@b.com\n"
       "100235,TITOS HANDMADE TEXAS VODKA,TITOS HANDMADE,36000291452,1.75 L,40,$34.99,0.28,c@d.com\n"
       "100236,JACK DANIELS OLD NO 7,JACK DANIELS,082184090565,750 ML,80,$27.49,0.22,e@f.com\n"
       "100237,KENDALL JACKSON CHARDONNAY 2019,KENDALL JACKSON,000000000000,750 ML,13.5,$14.99,0.19,g@h.com\n"
       "100238,HERRADURA ANEJO,HERRADURA,081753817107,750 ML,40,$59.99,0.24,i@j.com\n"
       "100239,VEUVE CLICQUOT BRUT,VEUVE CLICQUOT,081584000228,750 ML,12,$59.99,0.33,k@l.com\n")

MASTER = overlay_match.MemoryMaster(items={
    619947000020: {"brand": "Tito's Handmade", "product_name": "Texas Vodka", "category": "Vodka",
                   "size_ml": 750},
    36000291452: {"brand": "Acme", "product_name": "Acme Gin", "category": "Gin", "size_ml": 1750},
    82184090565: {"brand": "Jack Daniel's", "product_name": "Old No. 7", "category": "Whiskey",
                  "size_ml": 750},
    81753817107: {"brand": "Herradura", "product_name": "Añejo", "category": "Tequila", "size_ml": 750},
    81584000228: {"brand": "Veuve Clicquot", "product_name": "Brut", "category": "Sparkling Wine",
                  "size_ml": 750},
})

RES = overlay.run("book.csv", CSV.encode("utf-8"), tier="free",
                  master=MASTER, obs=overlay_market.MemoryObs())
ok("pipeline runs end to end", RES.get("ok"), RES.get("error"))

# 1. THE ORIGINAL FILE IS NEVER MODIFIED. Every value the user sent comes back byte-identical.
orig = [line.split(",") for line in CSV.strip().split("\n")[1:]]
ok("original values are returned verbatim", RES["_rows"] == orig,
   "%r != %r" % (RES["_rows"][:1], orig[:1]))

# 2. Nothing the mapper called PII is ever read into a stage, nor echoed in a profile.
pii = [c for c in RES["columns"] if c["bucket"] == "pii"]
ok("PII column detected", len(pii) == 1 and pii[0]["name"] == "Rep Email", [c["name"] for c in pii])
ok("PII values never echoed", all(c["sample"] == [] for c in pii))
ok("PII never mapped to a master field", all(c.get("field") is None for c in pii))
mapped_names = {c["name"] for c in RES["columns"] if c["bucket"] == "mapped"}
ok("PII never enters the mapped view", "Rep Email" not in mapped_names)

# 3. Every finding shown carries a root cause, a fix, and its arithmetic. No symptoms.
fs = RES["diagnose"]["findings"]
ok("findings exist on a file with known defects", len(fs) >= 4, [f["id"] for f in fs])
ok("every finding names a root cause", all(f["root_cause"] for f in fs))
ok("every finding names a fix", all(f["fix"] for f in fs))
ok("every finding shows its arithmetic",
   all(any(e.get("arithmetic") for e in f["evidence"]) for f in fs),
   [f["id"] for f in fs if not any(e.get("arithmetic") for e in f["evidence"])])

# 4. The precision gate: nothing DETERMINISTIC is shown below 0.98, nothing INFERENCE below 0.90,
#    and an unmeasured heuristic is silent rather than optimistic.
for f in fs:
    bar = 0.98 if f["label"] == "DETERMINISTIC" else 0.90
    ok("precision gate — %s" % f["id"], (f.get("precision") or 0) >= bar,
       "%s at %s" % (f["label"], f.get("precision")))
cat = overlay_detect.catalog(measured={})
ok("unmeasured heuristics are silent by default",
   set(cat["silent"]) == {"upc_owner_mismatch", "abv_out_of_category", "state_variant_sku"},
   cat["silent"])
ok("every silent detector says WHY it is silent",
   all(s.get("why") for s in RES["diagnose"]["silent"]))

# 5. The match rate is reported BY TIER, and the tiers sum to the row count.
m = RES["match"]
ok("tiers sum to the row count", sum(m["tiers"].values()) == m["rows"], m["tiers"])
ok("deterministic share excludes tier 4",
   m["deterministic"] == m["tiers"]["1"] + m["tiers"]["2"] + m["tiers"]["3"])
ok("every unmatched row carries a reason",
   all(mm.get("why") for mm in RES["matches"] if mm["match_tier"] == 5))
ok("the why-histogram accounts for every unmatched row",
   sum(w["rows"] for w in m["why"]) == m["unmatched"], m["why"])

# 6. A placeholder / bad-check code can never become an identity.
bad = [i for i, r in enumerate(RES["_rows"]) if r[3] in ("000000000000",)]
ok("placeholder barcode is never an identity",
   all(RES["matches"][i]["hoodie_id"] is None for i in bad))

# 7. The free tier locks what it says it locks — and locked means the VALUE is absent, not hidden.
enr_cols = RES["enrich"]["columns"]
locked = [c["column"] for c in enr_cols if c["locked"]]
ok("free tier locks the non-free enrichment columns", len(locked) == 7, locked)
ok("no enrichment column duplicates a cleanse column",
   not ({c["column"] for c in enr_cols} & {k for row in RES["clean"] for k in row}), enr_cols)
ok("visible enrichment columns actually resolve for a matched row",
   any(RES["enrich"]["rows"][i].get("hoodie_category") for i, m in enumerate(RES["matches"])
       if m.get("hoodie_id")))
ok("locked columns carry no real value",
   all(row[c] in (overlay.LOCKED_VALUE, "") for row in RES["enrich"]["rows"] for c in locked))

# 8. The market read is silent when it cannot clear its bar, and says why in a sentence.
ok("market blocks are absent, not padded", RES["market"]["blocks"] == [] or
   all(b["n"] and b["geography"] and b["freshness"] and b["method"] for b in RES["market"]["blocks"]))
ok("every withheld block explains itself", all(w["why"] for w in RES["market"]["withheld"]))

# 9. The workbook: five sheets, valid xlsx, and the provenance sheet is not optional.
BOOK = overlay.workbook(RES, master_build="2026-08-02",
                        quality={"precision": 0.99, "recall": 0.94},
                        xwalk={"rows": 32446, "distributors": 301})
with zipfile.ZipFile(io.BytesIO(BOOK)) as z:
    names = z.namelist()
    ok("workbook has 5 sheets", sum(1 for n in names if n.startswith("xl/worksheets/")) == 5, names)
    prov = z.read("xl/worksheets/sheet5.xml").decode("utf-8")
    for want in ("Detector registry version", "The honest boundary", "Match — deterministic",
                 "precision=0.99", "distributors=301"):
        ok("provenance sheet states %r" % want, want in prov)
    ok("free-tier retention is stated in the artifact", "No row data was stored" in prov)
    findings = z.read("xl/worksheets/sheet3.xml").decode("utf-8")
    ok("findings sheet shows the arithmetic", "÷ 2" in findings or "mod-10" in findings)
    for n in names:                                        # every part must be well-formed XML
        try:
            import xml.dom.minidom as _md
            _md.parseString(z.read(n))
        except Exception as e:                             # noqa: BLE001
            ok("workbook part %s is valid XML" % n, False, str(e))
            break
    else:
        ok("every workbook part is valid XML", True)

# 10. A 25k-row file resolves well inside the wall-clock budget (the speed IS the proof).
BIG = "Item,Brand,UPC,Size,ABV\n" + "".join(
    "SKU%d,Brand %d,%s,750 ML,40\n" % (i, i % 300, "0819947000%03d" % (i % 1000))
    for i in range(25000))
big = overlay.run("big.csv", BIG.encode("utf-8"), master=MASTER, obs=overlay_market.MemoryObs())
ok("25k rows parse and resolve", big.get("ok"), big.get("error"))
ok("25k rows inside the wall-clock budget (%.2fs)" % big["run"]["seconds"],
   big["run"]["seconds"] < 10.0)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
