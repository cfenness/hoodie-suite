"""Exercise the gold-set builder.

A gold set that agrees with the matcher by construction measures nothing, so the properties asserted
here are mostly about keeping the human's judgement independent of the machine's. Pure stdlib."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xsource_gold as xg  # noqa: E402

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def r(rid, brand, name, size, source, upc=""):
    return {"resolved_id": rid, "brand": brand, "name": name, "size": size,
            "source": source, "upc": upc}


ROWS = [
    r("A", "Tito's", "Tito's Handmade Vodka 750ml", "750ml", "binnys"),
    r("B", "Titos", "Titos Handmade Vodka 750 ML", "750 ML", "total-wine"),
    r("C", "Absolut", "Absolut Citron 750ml", "750ml", "binnys"),
    r("D", "Absolut", "Absolut Citron Vodka 750 ml", "750 ml", "abc"),
    r("E", "Absolut", "Absolut Mandrin 750ml", "750ml", "binnys"),
    r("F", "Absolut", "Absolut Citron 1750ml", "1750ml", "abc"),
    r("G", "Jameson", "Jameson Irish Whiskey 750ml", "750ml", "binnys"),
    r("H", "Jameson", "Jameson Black Barrel 750ml", "750ml", "abc"),
    r("I", "Jameson", "Jameson Irish Whiskey 1750ml", "1750ml", "haskells"),
]

print("candidates:")
c = xg.candidates(ROWS, n=30, seed=7, log=lambda *a: None)
check(c, "candidates are produced (%d)" % len(c))
strata = {p["stratum"] for p in c}
check("merged" in strata, "a MERGED stratum exists (measures precision)")
check("near_miss" in strata, "a NEAR_MISS stratum exists (measures recall — what the rule declined)")
check("control" in strata, "a CONTROL stratum exists (audits the labeller)")

print("\n  the label column must ship EMPTY:")
check(all(p["label"] == "" for p in c),
      "no pair arrives pre-labelled — a pre-filled answer produces rubber-stamping")
check(all(p["suggested"] == "" for p in c),
      "and with the LLM off there is no suggestion either, rather than a fabricated one")
check("label" in xg.SHEET_COLS and xg.SHEET_COLS[-1] == "label",
      "the answer column is LAST in the sheet, after the evidence")
check(xg.SHEET_COLS.index("suggested") < xg.SHEET_COLS.index("label"),
      "the machine's opinion sits in its OWN column, never the answer column")

print("\n  controls have a known answer:")
ctrl = [p for p in c if p["stratum"] == "control"]
check(ctrl, "controls were sampled (%d)" % len(ctrl))
check(all(str(p["a_size"]) != str(p["b_size"]) for p in ctrl),
      "every control pair is DIFFERENT-size, so the answer is always 'n'")
# The audit only works if the control is SUBTLE. Same brand + different product is obviously "n"
# and a labeller gets it right without reading — it tests nothing.
import xsource_match as _xm
for p in ctrl:
    sa = _xm.signature(p["a_brand"], p["a_name"], p["a_size"])
    sb = _xm.signature(p["b_brand"], p["b_name"], p["b_size"])
    check(sa and sb and sa[0] == sb[0] and sa[1] == sb[1] and sa[2] != sb[2],
          "control is the SAME PRODUCT at a different size (the subtle case), not two obviously "
          "different products")

print("\n  reproducibility:")
c2 = xg.candidates(ROWS, n=30, seed=7, log=lambda *a: None)
check([p["pair_id"] for p in c] == [p["pair_id"] for p in c2],
      "the same seed produces the same sheet — a half-finished sheet is never invalidated")
c3 = xg.candidates(ROWS, n=30, seed=99, log=lambda *a: None)
check([p["pair_id"] for p in c] != [p["pair_id"] for p in c3] or len(c) < 3,
      "a different seed produces a different sample")

print("\nround-trip:")
tmp = os.path.join(tempfile.mkdtemp(), "gold.csv")
xg.export(c, tmp, log=lambda *a: None)
check(os.path.exists(tmp), "the sheet writes")
lab = xg.read_labels(tmp)
check(len(lab) == len(c), "every pair reads back (%d)" % len(lab))
check(all(v["label"] is None for v in lab.values()),
      "an unfilled sheet reads back as UNLABELLED, not as 'no'")

# fill it in, including one deliberately wrong control
import csv as _csv
with open(tmp, encoding="utf-8") as f:
    rows = list(_csv.DictReader(f))
for i, row in enumerate(rows):
    row["label"] = "y" if row["stratum"] == "merged" else ("y" if row["stratum"] == "control" and i == len(rows) - 1 else "n")
with open(tmp, "w", newline="", encoding="utf-8") as f:
    w = _csv.DictWriter(f, fieldnames=xg.SHEET_COLS)
    w.writeheader()
    for row in rows:
        w.writerow({k: row.get(k, "") for k in xg.SHEET_COLS})

pairs, rep = xg.ingest(tmp, pairs=[dict(p) for p in c], land=False, log=lambda *a: None)
check(rep["labelled"] > 0, "labels attach (%d)" % rep["labelled"])
check(rep["controls_wrong"] >= 1,
      "a control answered wrongly is DETECTED (%d) — the sheet audits its own labeller"
      % rep["controls_wrong"])
check(rep["control_accuracy"] is not None and rep["control_accuracy"] < 1.0,
      "and control accuracy is reported (%s), not folded into the score" % rep["control_accuracy"])

print("\nscoring is PER STRATUM:")
sc = xg.score(pairs, log=lambda *a: None)
check(isinstance(sc, dict) and "merged" in sc, "the merged stratum is scored on its own")
check("ALL" in sc, "an overall figure exists alongside, never instead of, the strata")
check(all(("precision" in v and "recall" in v) for k, v in sc.items()),
      "each stratum reports precision AND recall")
check(xg.score([], log=lambda *a: None)["status"] == "no_labels",
      "an unlabelled set scores NOTHING rather than 100%")

print("\nvalue parsing:")
check(xg.VALID.get("y") is True and xg.VALID.get("n") is False, "y/n parse")
check(xg.VALID.get("?") is None and xg.VALID.get("") is None,
      "'?' and blank are UNKNOWN — never coerced into a judgement")

print("\na sheet a HUMAN actually edited (Excel headers, added columns, partial fills):")
import csv as _c, os as _o, tempfile as _t, json as _j
_p = _o.path.join(_t.mkdtemp(), "human.csv")
# Exactly the shape observed live: capitalised "Label", extra canonical columns, and only SOME rows
# carrying canonical values — "I'll only fill the dimensions I'm confident on".
_hdr = ["pair_id","stratum","a_source","a_brand","a_name","a_size","b_source","b_brand","b_name",
        "b_size","suggested","suggest_reason","Chris Brand Name","Chris Product Name",
        "Chris Pack Size","Category","Label"]
_ids = [p["pair_id"] for p in c[:3]]
with open(_p,"w",newline="",encoding="utf-8") as f:
    w = _c.writer(f); w.writerow(_hdr)
    w.writerow([_ids[0],"merged","binnys","Baron Herzog","Baron Herzog Sauvignon Blanc","750ML",
                "abc","Baron Herzog","Baron Herzog Sauvignon Blanc","750 mL","","",
                "Baron Herzog","Baron Herzog Sauvignon Blanc","750ML","Wine","Y"])
    w.writerow([_ids[1],"merged","binnys","Zuccardi","Zuccardi Serie A Torrontes","750ML",
                "abc","Zuccardi","Zuccardi Serie A Torrontes","750 mL","","","","","","","Y"])
    w.writerow([_ids[2],"merged","abc","X","X thing","750ML","binnys","Y","Y thing","750ML","","",
                "","","","","n"])
_lab = xg.read_labels(_p)
check(len(_lab) == 3, "a human-edited sheet reads back (%d rows)" % len(_lab))
check(_lab[_ids[0]]["label"] is True,
      "a capitalised 'Label' column with 'Y' is read — an exact-match reader would have seen an "
      "entirely EMPTY sheet")
check(_lab[_ids[2]]["label"] is False, "'n' reads as False")
check(_lab[_ids[0]].get("canon_brand") == "Baron Herzog",
      "an added 'Chris Brand Name' column maps to canon_brand")
check(_lab[_ids[0]].get("canon_product") == "Baron Herzog Sauvignon Blanc", "...and product")
check(_lab[_ids[0]].get("canon_size") == "750ML", "...and pack size")
check(_lab[_ids[0]].get("canon_category") == "Wine", "...and a category column he added later")
check("canon_brand" not in _lab[_ids[1]],
      "a row labelled but with NO canonical values stays silent — partial filling is expected, and "
      "a blank is never read as 'the correct brand is empty'")

_pairs2, _rep2 = xg.ingest(_p, pairs=[dict(p) for p in c], land=False, log=lambda *a: None)
check(_rep2["labelled"] == 3, "all three labels attach (%d)" % _rep2["labelled"])
check(_rep2["canonical_values"] == 1, "and the canonical-value count is reported separately (%d)"
      % _rep2["canonical_values"])
_got = {p["pair_id"]: p for p in _pairs2}
check(_got[_ids[0]]["canon_brand"] == "Baron Herzog", "canonical values land on the row")

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
